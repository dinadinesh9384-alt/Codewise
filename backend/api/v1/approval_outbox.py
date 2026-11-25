"""Durable approval outbox claiming, checkpoint reconciliation, and finalization."""

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.repositories.models import (ApprovalDecision, ApprovalOutboxEvent,
                                     WorkflowExecution)

LEASE_DURATION = timedelta(minutes=10)
MAX_ATTEMPTS = 12


def _claim(event_id):
    now = timezone.now()
    with transaction.atomic():
        query = ApprovalOutboxEvent.objects.select_for_update()
        if connection.vendor == "postgresql":
            query = query.skip_locked()
        event = query.select_related("execution", "workspace", "actor").filter(pk=event_id).first()
        if event is None or event.status == ApprovalOutboxEvent.Status.COMPLETED:
            return None
        if event.status == ApprovalOutboxEvent.Status.FAILED:
            return None
        if event.attempts >= MAX_ATTEMPTS:
            event.status = ApprovalOutboxEvent.Status.FAILED
            event.lease_until = None
            event.last_error_code = "approval_retry_limit_reached"
            event.save(update_fields=("status", "lease_until", "last_error_code", "updated_at"))
            return None
        if (event.status == ApprovalOutboxEvent.Status.PROCESSING and event.lease_until and
                event.lease_until > now):
            return None
        event.status = ApprovalOutboxEvent.Status.PROCESSING
        event.attempts += 1
        event.lease_until = now + LEASE_DURATION
        event.last_error_code = ""
        event.save(update_fields=("status", "attempts", "lease_until", "last_error_code", "updated_at"))
        return event.id, event.attempts


def _mark_failure(event_id, attempt, code, *, terminal=False):
    with transaction.atomic():
        event = ApprovalOutboxEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.status != ApprovalOutboxEvent.Status.PROCESSING or event.attempts != attempt:
            return
        event.status = (ApprovalOutboxEvent.Status.FAILED if terminal else
                        ApprovalOutboxEvent.Status.RETRYABLE)
        event.lease_until = None
        event.last_error_code = code
        event.save(update_fields=("status", "lease_until", "last_error_code", "updated_at"))


def _after_checkpoint_advance(event):
    """Failure injection seam; production implementation deliberately does nothing."""


def _after_decision_persistence(event):
    """Failure injection seam inside the Django finalization transaction."""


def process_approval_outbox(event_id):
    """Idempotently apply one durable approval command and finalize Django records.

    Checkpoint state is validated against the immutable command before it can be
    resumed or continued. The checkpoint and Django database remain separate
    transactions; the outbox allows recovery by inspecting the checkpoint.
    """
    from api.v1.services import (APIServiceError, _configured_runtime,
                                 _validate_agent_outputs,
                                 validate_approval_proposal_set)
    from orchestration.contracts import WorkflowRequest
    from orchestration.graph import resume_or_recover_workflow

    claimed = _claim(event_id)
    if claimed is None:
        event = ApprovalOutboxEvent.objects.filter(pk=event_id).select_related("execution").first()
        return event.execution if event and event.status == ApprovalOutboxEvent.Status.COMPLETED else None
    _event_id, attempt = claimed
    terminal = False
    error_code = "approval_processing_failed"
    runtime = None
    try:
        event = ApprovalOutboxEvent.objects.select_related(
            "execution__binding__snapshot", "execution__binding__repository", "execution__submission",
            "actor", "workspace").get(pk=event_id)
        execution = event.execution
        binding = execution.binding
        snapshot = binding.snapshot
        if (execution.id != event.execution_id or execution.workspace_id != event.workspace_id or
                binding.snapshot_id != event.snapshot_id or snapshot.id != event.snapshot_id or
                binding.commit_sha != snapshot.commit_sha):
            terminal, error_code = True, "approval_execution_snapshot_mismatch"
            raise ValueError
        if execution.status != WorkflowExecution.Status.PENDING_APPROVAL or not execution.result:
            terminal, error_code = True, "approval_execution_state_conflict"
            raise ValueError
        validate_approval_proposal_set(execution, snapshot, event.proposal_ids)

        runtime = _configured_runtime()
        if runtime is None or not getattr(runtime, "durable_approval_supported", False):
            error_code = "durable_approval_checkpoint_unavailable"
            raise RuntimeError
        request = WorkflowRequest(execution_id=execution.id, workspace_id=event.workspace_id,
            repository_id=binding.repository_id, snapshot_id=snapshot.id,
            commit_sha=snapshot.commit_sha, manifest_digest=snapshot.manifest_digest,
            correlation_id=f"approval-{event.id}", trace_id=f"approval-{event.id}",
            groups=frozenset(execution.selected_groups), approval_required=True)
        workflow = runtime.workflow_for(execution=execution, snapshot=snapshot)
        if not getattr(workflow, "durable_checkpointer", False):
            error_code = "durable_approval_checkpoint_unavailable"
            raise RuntimeError
        config = {"configurable": {"thread_id": str(execution.id)}}
        result = resume_or_recover_workflow(workflow, request, event.decision, config=config)
        value = result.model_dump(mode="json")
        _validate_agent_outputs(result, request)
        _after_checkpoint_advance(event)

        with transaction.atomic():
            locked_event = ApprovalOutboxEvent.objects.select_for_update().get(pk=event_id)
            if (locked_event.status != ApprovalOutboxEvent.Status.PROCESSING or
                    locked_event.attempts != attempt):
                return None
            locked_execution = WorkflowExecution.objects.select_for_update().get(pk=execution.id)
            for proposal_id in locked_event.proposal_ids:
                existing = ApprovalDecision.objects.filter(execution=locked_execution,
                                                           proposal_id=proposal_id).first()
                if existing is not None:
                    if (existing.snapshot_id != locked_event.snapshot_id or
                            existing.actor_id != locked_event.actor_id or
                            existing.decision != locked_event.decision):
                        terminal, error_code = True, "approval_decision_conflict"
                        raise ValueError
                    continue
                ApprovalDecision.objects.create(execution=locked_execution,
                    workspace_id=locked_event.workspace_id, proposal_id=proposal_id,
                    snapshot_id=locked_event.snapshot_id, actor=locked_event.actor,
                    decision=locked_event.decision)
            _after_decision_persistence(locked_event)
            locked_execution.result = value
            locked_execution.status = (WorkflowExecution.Status.COMPLETED
                if result.status == "completed" else WorkflowExecution.Status.PARTIAL)
            locked_execution.finished_at = timezone.now()
            locked_execution.save(update_fields=("result", "status", "finished_at", "updated_at"))
            from apps.repositories.services import create_audit_event
            create_audit_event(workspace_id=locked_event.workspace_id, actor=locked_event.actor,
                action="human_patch_approval_decision", outcome=locked_event.decision,
                reason_code="authenticated_human_decision",
                repository_id_ref=str(binding.repository_id),
                submission_id_ref=str(locked_execution.submission_id),
                snapshot_id_ref=str(locked_event.snapshot_id), correlation_id=str(locked_event.id))
            locked_event.status = ApprovalOutboxEvent.Status.COMPLETED
            locked_event.completed_at = timezone.now()
            locked_event.lease_until = None
            locked_event.last_error_code = ""
            locked_event.save(update_fields=("status", "completed_at", "lease_until",
                                              "last_error_code", "updated_at"))
        return locked_execution
    except APIServiceError as exc:
        error_code = exc.code
        terminal = exc.status < 500
    except ValueError:
        terminal, error_code = True, "approval_checkpoint_identity_conflict"
    except Exception:
        # Do not persist exception text: SDK/DB exceptions can contain secrets.
        pass
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    _mark_failure(event_id, attempt, error_code, terminal=terminal)
    return None
