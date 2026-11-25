"""Integration services for authenticated API operations."""
import hashlib
import importlib
import inspect
import json
import logging
import re
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.repositories.models import (ApprovalDecision, ApprovalOutboxEvent, Repository, RepositorySnapshot,
                                      RepositorySubmission, WorkflowExecution)
from apps.repositories.services import (create_audit_event, create_execution_snapshot_binding,
                                        get_authorized_snapshot, require_workspace_membership)
from orchestration.contracts import WorkflowRequest
from orchestration.graph import run_workflow

logger = logging.getLogger("codewise.api")


class APIServiceError(Exception):
    def __init__(self, code, status=400):
        self.code = code
        self.status = status
        super().__init__(code)


def _fingerprint(value):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _idempotency_key(request):
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value or len(value) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise APIServiceError("idempotency_key_required", 400)
    return value


def _configured_runtime():
    factory = getattr(settings, "CODEWISE_API_WORKFLOW_FACTORY", None)
    if not factory:
        return None
    try:
        if isinstance(factory, str):
            module, name = factory.rsplit(".", 1)
            factory = getattr(importlib.import_module(module), name)
        runtime = factory()
        if runtime is None or not callable(getattr(runtime, "workflow_for", None)):
            raise TypeError
        return runtime
    except Exception:
        logger.warning("Configured workflow runtime is unavailable.")
        return None


def _public_submission(*, user, workspace_id, repository_url, ref, ref_kind, submission):
    """Acquire and seal a public GitHub snapshot using existing provider/storage services."""
    from apps.repositories.providers.github import GitHubClient
    from apps.repositories.providers.github.extraction import extract_staged_archive
    from apps.repositories.providers.github.manifest import build_repository_manifest
    from apps.repositories.providers.github.snapshot_adapter import GitHubManifestSnapshotAdapter
    from apps.repositories.providers.github.urls import parse_repository_url
    from apps.repositories.snapshot_service import store_and_seal_snapshot
    from apps.repositories.storage.local import LocalSnapshotStorage

    locator = parse_repository_url(repository_url)
    client = GitHubClient()
    metadata = client.get_public_repository(repository_url)
    resolved = client.resolve_reference(metadata, ref, kind=ref_kind) if ref else client.resolve_branch(
        metadata, metadata.default_branch or "")
    repository, _ = Repository.objects.get_or_create(
        workspace_id=workspace_id, provider="github", provider_repository_id=metadata.provider_repository_id,
        defaults={"owner": metadata.owner, "name": metadata.name, "canonical_url": metadata.canonical_url,
                  "visibility": metadata.visibility, "default_branch": metadata.default_branch or ""})
    if (repository.owner.casefold() != locator.owner.casefold() or repository.name.casefold() != locator.name.casefold()
            or repository.visibility != "public"):
        raise APIServiceError("repository_identity_conflict", 409)
    submission.repository = repository
    submission.status = RepositorySubmission.Status.RETRIEVING
    submission.save(update_fields=("repository", "status", "updated_at"))
    snapshot = RepositorySnapshot.objects.create(workspace_id=workspace_id, repository=repository,
        source_grant=None, commit_sha=resolved.commit_sha, visibility_at_capture=Repository.Visibility.PUBLIC,
        status=RepositorySnapshot.Status.RESOLVING,
        manifest_schema_version="repository-manifest.v1")
    storage = LocalSnapshotStorage()
    adapter = GitHubManifestSnapshotAdapter()
    with client.stage_public_archive(resolved) as staged:
        with extract_staged_archive(staged) as extracted:
            manifest = build_repository_manifest(resolved_commit=resolved, staged_archive=staged,
                                                   extracted_archive=extracted)
            sealed = store_and_seal_snapshot(user=user, workspace_id=workspace_id, snapshot_id=snapshot.id,
                resolved_commit=resolved, staged_archive=staged, extracted_archive=extracted,
                manifest=manifest, manifest_codec=adapter, storage=storage)
    if sealed.status not in {"sealed", "already_sealed"}:
        submission.status = RepositorySubmission.Status.FAILED
        submission.error_code = sealed.error_code or "snapshot_sealing_failed"
        submission.save(update_fields=("status", "error_code", "updated_at"))
        raise APIServiceError(submission.error_code, 502)
    submission.snapshot = snapshot
    submission.status = RepositorySubmission.Status.SEALED
    submission.save(update_fields=("snapshot", "status", "updated_at"))
    return snapshot


def submit_repository(*, user, workspace_id, payload, key):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    from apps.repositories.providers.github.urls import parse_repository_url
    try:
        locator = parse_repository_url(payload["repository_url"])
    except Exception:
        raise APIServiceError("invalid_repository_url", 400) from None
    fingerprint = _fingerprint(payload)
    existing = RepositorySubmission.objects.filter(workspace_id=workspace_id, idempotency_key=key).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise APIServiceError("idempotency_key_conflict", 409)
        return existing, existing.snapshot, False
    with transaction.atomic():
        submission = RepositorySubmission(workspace_id=workspace_id, submitted_by=user,
            submitted_url=payload["repository_url"],
            normalized_locator=f"{locator.owner.casefold()}/{locator.name.casefold()}",
            idempotency_key=key, request_fingerprint=fingerprint)
        submission.full_clean()
        try:
            with transaction.atomic():
                submission.save()
        except IntegrityError:
            existing = RepositorySubmission.objects.filter(workspace_id=workspace_id, idempotency_key=key).first()
            if existing and existing.request_fingerprint == fingerprint:
                return existing, existing.snapshot, False
            raise APIServiceError("idempotency_key_conflict", 409)
    try:
        snapshot = _public_submission(user=user, workspace_id=workspace_id, repository_url=payload["repository_url"],
                                      ref=payload.get("ref", ""), ref_kind=payload.get("ref_kind", "branch"),
                                      submission=submission)
        return submission, snapshot, True
    except APIServiceError:
        raise
    except Exception as exc:
        submission.status = RepositorySubmission.Status.FAILED
        submission.error_code = getattr(exc, "code", "repository_submission_failed")
        if not isinstance(submission.error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", submission.error_code):
            submission.error_code = "repository_submission_failed"
        submission.save(update_fields=("status", "error_code", "updated_at"))
        status = 502 if submission.error_code.startswith(("github_", "snapshot_")) else 400
        raise APIServiceError(submission.error_code, status) from None


def _workflow_result_is_success(result, selected_agents):
    # Lifecycle completion alone is insufficient: every selected stage needs a result.
    agents = {row.agent_id: row for row in result.agents}
    return result.status == "completed" and all(
        agents.get(agent_id) is not None and agents[agent_id].status == "completed" and
        agents[agent_id].output is not None for agent_id in selected_agents)


def _validate_agent_outputs(result, request):
    from importlib import import_module
    output_models = {
        "structure_analyzer": ("agents.01_structure_analyzer.schemas.output", "StructureAnalysisOutput"),
        "business_logic_analyst": ("agents.02_business_logic_analyst.schemas.output", "BusinessAnalysisOutput"),
        "code_quality_analyst": ("agents.03_code_quality_analyst.schemas.output", "CodeQualityOutput"),
        "security_analyst": ("agents.04_security_analyst.schemas.output", "SecurityAnalysisOutput"),
        "migration_planner": ("agents.05_migration_planner.schemas.output", "MigrationPlanOutput"),
        "patch_author": ("agents.06_patch_author.schemas.output", "PatchAuthorOutput"),
        "test_engineer": ("agents.07_test_engineer.schemas.output", "TestEngineerOutput"),
        "review_agent": ("agents.08_review_agent.schemas.output", "ReviewAgentOutput"),
    }
    for agent in result.agents:
        if not agent.output:
            continue
        module_name, output_name = output_models[agent.agent_id]
        output_model = getattr(import_module(module_name), output_name)
        parsed = output_model.model_validate(agent.output)
        if (parsed.execution_id != uuid.uuid5(request.execution_id, agent.agent_id) or
                parsed.repository_id != request.repository_id or
                parsed.snapshot_id != request.snapshot_id or parsed.commit_sha != request.commit_sha or
                parsed.manifest_digest != request.manifest_digest):
            raise APIServiceError("agent_result_identity_mismatch", 500)


def start_execution(*, user, workspace_id, payload, key):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    submission = RepositorySubmission.objects.filter(pk=payload["submission_id"], workspace_id=workspace_id).select_related("repository").first()
    if submission is None or submission.status != RepositorySubmission.Status.SEALED or not submission.repository_id:
        raise APIServiceError("submission_not_ready", 409)
    snapshot = submission.snapshot
    if snapshot is None:
        raise APIServiceError("sealed_snapshot_not_found", 409)
    get_authorized_snapshot(user=user, workspace_id=workspace_id, snapshot_id=snapshot.id)
    groups = sorted(set(payload["groups"]))
    request_body = {"submission_id": str(submission.id), "groups": groups,
                    "approval_required": payload["approval_required"]}
    fingerprint = _fingerprint(request_body)
    prior = WorkflowExecution.objects.filter(workspace_id=workspace_id, idempotency_key=key).first()
    if prior:
        if prior.request_fingerprint != fingerprint:
            raise APIServiceError("idempotency_key_conflict", 409)
        return prior, False
    with transaction.atomic():
        snapshot = RepositorySnapshot.objects.select_for_update().get(pk=snapshot.pk)
        existing = WorkflowExecution.objects.filter(workspace_id=workspace_id, idempotency_key=key).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise APIServiceError("idempotency_key_conflict", 409)
            return existing, False
        execution_id = uuid.uuid4()
        selected_agents = ["structure_analyzer", "business_logic_analyst"]
        for agent_id, group in (("code_quality_analyst", "code_quality_security_planning"),
                                ("security_analyst", "code_quality_security_planning"),
                                ("migration_planner", "code_quality_security_planning"),
                                ("patch_author", "patch_engineering"), ("test_engineer", "patch_engineering"),
                                ("review_agent", "final_review")):
            if group in groups:
                selected_agents.append(agent_id)
        if payload["approval_required"] and not {"patch_author", "review_agent"} <= set(selected_agents):
            raise APIServiceError("approval_agents_not_selected", 400)
        try:
            with transaction.atomic():
                binding = create_execution_snapshot_binding(user=user, workspace_id=workspace_id,
                    execution_id=execution_id, repository=submission.repository, snapshot=snapshot,
                    agent_ids=selected_agents, agent_group_ids=groups)
                record = WorkflowExecution(id=execution_id, workspace_id=workspace_id, submission=submission,
                    binding=binding, requested_by=user, status=WorkflowExecution.Status.QUEUED,
                    selected_groups=groups, selected_agents=selected_agents,
                    idempotency_key=key, request_fingerprint=fingerprint)
                record.full_clean()
                record.save()
        except IntegrityError:
            existing = WorkflowExecution.objects.filter(workspace_id=workspace_id, idempotency_key=key).first()
            if existing and existing.request_fingerprint == fingerprint:
                return existing, False
            raise APIServiceError("idempotency_key_conflict", 409) from None
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_queued",
            outcome="queued", reason_code="snapshot_bound", repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=str(execution_id))
    runtime = _configured_runtime()
    if runtime is None:
        record.status = WorkflowExecution.Status.FAILED
        record.error_code = "workflow_runtime_unavailable"
        record.finished_at = timezone.now()
        record.save(update_fields=("status", "error_code", "finished_at", "updated_at"))
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_failed",
            outcome="failed", reason_code=record.error_code, repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=str(record.id))
        raise APIServiceError("workflow_runtime_unavailable", 503)
    if payload["approval_required"] and not getattr(runtime, "durable_approval_supported", False):
        record.status = WorkflowExecution.Status.FAILED
        record.error_code = "durable_approval_checkpoint_unavailable"
        record.finished_at = timezone.now()
        record.save(update_fields=("status", "error_code", "finished_at", "updated_at"))
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_failed",
            outcome="failed", reason_code=record.error_code, repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=str(record.id))
        raise APIServiceError(record.error_code, 503)
    now = timezone.now()
    record.status = WorkflowExecution.Status.RUNNING
    record.started_at = now
    record.save(update_fields=("status", "started_at", "updated_at"))
    correlation_id = str(uuid.uuid4())
    create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_started",
        outcome="running", reason_code="runtime_started", repository_id_ref=str(submission.repository_id),
        submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=correlation_id)
    request = WorkflowRequest(execution_id=execution_id, workspace_id=workspace_id,
        repository_id=submission.repository_id, snapshot_id=snapshot.id, commit_sha=snapshot.commit_sha,
        manifest_digest=snapshot.manifest_digest, correlation_id=correlation_id,
        trace_id=str(uuid.uuid4()), groups=frozenset(groups), approval_required=payload["approval_required"])
    try:
        def persist_progress(event):
            agent_id = event.get("agent_id")
            status = event.get("status")
            if agent_id not in selected_agents or status not in {
                    "completed", "failed", "cancelled", "timed_out", "skipped", "unavailable"}:
                return
            with transaction.atomic():
                current = WorkflowExecution.objects.select_for_update().get(pk=record.pk)
                stored = current.result or {}
                progress = {row["agent_id"]: row for row in stored.get("agent_progress", ())}
                progress[agent_id] = {"agent_id": agent_id, "status": status,
                                      "error_code": event.get("error_code")}
                current.result = {**stored, "agent_progress": [progress[key] for key in selected_agents
                                                                   if key in progress]}
                current.save(update_fields=("result", "updated_at"))

        factory_parameters = inspect.signature(runtime.workflow_for).parameters
        if "observer" in factory_parameters:
            workflow = runtime.workflow_for(execution=record, snapshot=snapshot, observer=persist_progress)
        else:
            workflow = runtime.workflow_for(execution=record, snapshot=snapshot)
        if payload["approval_required"] and not getattr(workflow, "durable_checkpointer", False):
            raise APIServiceError("durable_approval_checkpoint_unavailable", 503)
        config = ({"configurable": {"thread_id": str(execution_id)}}
                  if getattr(workflow, "has_checkpointer", False) else None)
        result = run_workflow(workflow, request, config=config)
        _validate_agent_outputs(result, request)
        value = result.model_dump(mode="json")
        record.result = value
        if result.approval == "pending":
            record.status = WorkflowExecution.Status.PENDING_APPROVAL
        elif result.status == "failed":
            record.status = WorkflowExecution.Status.FAILED
        elif result.status == "cancelled":
            record.status = WorkflowExecution.Status.CANCELLED
        elif result.status == "timed_out":
            record.status = WorkflowExecution.Status.TIMED_OUT
        elif result.status == "completed" and _workflow_result_is_success(result, selected_agents):
            record.status = WorkflowExecution.Status.COMPLETED
        else:
            record.status = WorkflowExecution.Status.PARTIAL
        record.finished_at = timezone.now()
        record.save(update_fields=("result", "status", "finished_at", "updated_at"))
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_finished",
            outcome=record.status, reason_code="workflow_result_persisted", repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=correlation_id)
    except APIServiceError as exc:
        record.status = WorkflowExecution.Status.FAILED
        record.error_code = exc.code
        record.finished_at = timezone.now()
        record.save(update_fields=("status", "error_code", "finished_at", "updated_at"))
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_failed",
            outcome="failed", reason_code=record.error_code, repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=str(record.id))
        raise
    except Exception:
        record.status = WorkflowExecution.Status.FAILED
        record.error_code = "workflow_execution_failed"
        record.finished_at = timezone.now()
        record.save(update_fields=("status", "error_code", "finished_at", "updated_at"))
        create_audit_event(workspace_id=workspace_id, actor=user, action="workflow_execution_failed",
            outcome="failed", reason_code=record.error_code, repository_id_ref=str(submission.repository_id),
            submission_id_ref=str(submission.id), snapshot_id_ref=str(snapshot.id), correlation_id=correlation_id)
        raise APIServiceError(record.error_code, 500) from None
    return record, True


def validate_approval_proposal_set(record, snapshot, requested_ids):
    """Recheck Agent 6/8 integrity both when queued and when processed."""
    from importlib import import_module
    binding = record.binding
    result = record.result or {}
    if (str(result.get("execution_id")) != str(record.id) or
            str(binding.snapshot_id) != str(snapshot.id) or binding.commit_sha != snapshot.commit_sha):
        raise APIServiceError("execution_snapshot_identity_mismatch", 409)
    agent_rows = {item.get("agent_id"): item for item in result.get("agents", ()) if isinstance(item, dict)}
    try:
        patch_model = import_module("agents.06_patch_author.schemas.output").PatchAuthorOutput
        review_model = import_module("agents.08_review_agent.schemas.output").ReviewAgentOutput
        patch = patch_model.model_validate((agent_rows.get("patch_author") or {}).get("output"))
        review = review_model.model_validate((agent_rows.get("review_agent") or {}).get("output"))
    except Exception:
        raise APIServiceError("approval_review_invalid", 409) from None
    if (patch.execution_id != uuid.uuid5(record.id, "patch_author") or
            patch.repository_id != binding.repository_id or patch.snapshot_id != snapshot.id or
            patch.commit_sha != binding.commit_sha or patch.manifest_digest != snapshot.manifest_digest or
            patch.status != "awaiting_review" or patch.validation_status != "passed" or not patch.proposals):
        raise APIServiceError("approval_patch_invalid", 409)
    if (review.execution_id != uuid.uuid5(record.id, "review_agent") or
            review.repository_id != binding.repository_id or review.snapshot_id != snapshot.id or
            review.commit_sha != binding.commit_sha or review.manifest_digest != snapshot.manifest_digest or
            review.llm_status != "completed" or review.validation_errors or review.unavailable_agents or
            review.skipped_agents or review.failed_agents or review.partial_agents or
            review.review_status not in {"ready_for_human_review", "ready_with_warnings"}):
        raise APIServiceError("approval_review_not_ready", 409)
    selected = set(record.selected_agents)
    for row in review.reviewed_agents:
        upstream = agent_rows.get(row.agent_id) or {}
        if (row.status != upstream.get("status", "unavailable") or row.selected != (row.agent_id in selected) or
                row.output_present != (upstream.get("output") is not None)):
            raise APIServiceError("approval_review_provenance_mismatch", 409)
    verified_ids = {row.proposal_id for row in review.patch_reviews
                    if row.validation_status == "verified_in_memory" and not row.applied}
    proposal_ids = {row.proposal_id for row in patch.proposals}
    if not proposal_ids or proposal_ids != set(requested_ids) or not proposal_ids <= verified_ids:
        raise APIServiceError("approval_proposal_set_mismatch", 409)


def approve_execution(*, user, workspace_id, execution_id, payload):
    from importlib import import_module
    from apps.repositories.models import WorkspaceMembership
    from .approval_outbox import process_approval_outbox

    require_workspace_membership(user=user, workspace_id=workspace_id,
                                 roles={WorkspaceMembership.Role.OWNER, WorkspaceMembership.Role.ADMIN})
    with transaction.atomic():
        record = WorkflowExecution.objects.select_for_update().select_related(
            "binding__snapshot", "binding__repository", "submission").filter(
                pk=execution_id, workspace_id=workspace_id).first()
        if record is None:
            raise APIServiceError("execution_not_found", 404)
        prior_event = ApprovalOutboxEvent.objects.filter(execution=record).first()
        if prior_event is not None:
            submitted_fingerprint = _fingerprint({"execution_id": str(record.id),
                "snapshot_id": str(prior_event.snapshot_id), "decision": payload["decision"],
                "proposal_ids": sorted(payload["proposal_ids"])})
            if prior_event.request_fingerprint != submitted_fingerprint:
                raise APIServiceError("approval_already_recorded", 409)
            if prior_event.status == ApprovalOutboxEvent.Status.COMPLETED:
                return record
            if prior_event.status == ApprovalOutboxEvent.Status.FAILED:
                raise APIServiceError("approval_recovery_conflict", 409)
        if record.status != WorkflowExecution.Status.PENDING_APPROVAL or not record.result:
            raise APIServiceError("approval_not_pending", 409)
        binding = record.binding
        snapshot = get_authorized_snapshot(user=user, workspace_id=workspace_id, snapshot_id=binding.snapshot_id)
        result = record.result
        if (str(result.get("execution_id")) != str(record.id) or
                str(binding.snapshot_id) != str(snapshot.id) or binding.commit_sha != snapshot.commit_sha):
            raise APIServiceError("execution_snapshot_identity_mismatch", 409)
        agent_rows = {item.get("agent_id"): item for item in result.get("agents", ()) if isinstance(item, dict)}
        patch_raw = (agent_rows.get("patch_author") or {}).get("output")
        review_raw = (agent_rows.get("review_agent") or {}).get("output")
        try:
            patch_model = import_module("agents.06_patch_author.schemas.output").PatchAuthorOutput
            review_model = import_module("agents.08_review_agent.schemas.output").ReviewAgentOutput
            patch = patch_model.model_validate(patch_raw)
            review = review_model.model_validate(review_raw)
        except Exception:
            raise APIServiceError("approval_review_invalid", 409) from None
        if (patch.execution_id != uuid.uuid5(record.id, "patch_author") or
                patch.repository_id != binding.repository_id or patch.snapshot_id != snapshot.id or
                patch.commit_sha != binding.commit_sha or patch.manifest_digest != snapshot.manifest_digest or
                patch.status != "awaiting_review" or patch.validation_status != "passed" or not patch.proposals):
            raise APIServiceError("approval_patch_invalid", 409)
        if (review.execution_id != uuid.uuid5(record.id, "review_agent") or
                review.repository_id != binding.repository_id or review.snapshot_id != snapshot.id or
                review.commit_sha != binding.commit_sha or review.manifest_digest != snapshot.manifest_digest or
                review.llm_status != "completed" or review.validation_errors or review.unavailable_agents or
                review.skipped_agents or review.failed_agents or review.partial_agents or
                review.review_status not in {"ready_for_human_review", "ready_with_warnings"}):
            raise APIServiceError("approval_review_not_ready", 409)
        selected = set(record.selected_agents)
        for row in review.reviewed_agents:
            upstream = agent_rows.get(row.agent_id) or {}
            if (row.status != upstream.get("status", "unavailable") or row.selected != (row.agent_id in selected) or
                    row.output_present != (upstream.get("output") is not None)):
                raise APIServiceError("approval_review_provenance_mismatch", 409)
        verified_ids = {row.proposal_id for row in review.patch_reviews
                        if row.validation_status == "verified_in_memory" and not row.applied}
        proposal_ids = {row.proposal_id for row in patch.proposals}
        requested_ids = set(payload["proposal_ids"])
        if not proposal_ids or proposal_ids != requested_ids or not proposal_ids <= verified_ids:
            raise APIServiceError("approval_proposal_set_mismatch", 409)
        if ApprovalDecision.objects.filter(execution=record).exists():
            raise APIServiceError("approval_already_recorded", 409)
        runtime = _configured_runtime()
        if runtime is None or not getattr(runtime, "durable_approval_supported", False):
            raise APIServiceError("durable_approval_checkpoint_unavailable", 503)
        fingerprint = _fingerprint({"execution_id": str(record.id), "snapshot_id": str(snapshot.id),
                                    "decision": payload["decision"],
                                    "proposal_ids": sorted(requested_ids)})
        event = ApprovalOutboxEvent.objects.filter(execution=record).first()
        if event is not None:
            if event.request_fingerprint != fingerprint:
                raise APIServiceError("approval_already_recorded", 409)
        else:
            event = ApprovalOutboxEvent.objects.create(execution=record, workspace_id=workspace_id,
                snapshot_id=snapshot.id, actor=user, decision=payload["decision"],
                proposal_ids=sorted(requested_ids), request_fingerprint=fingerprint)
            create_audit_event(workspace_id=workspace_id, actor=user, action="human_patch_approval_requested",
                outcome="queued", reason_code="authenticated_human_decision",
                repository_id_ref=str(binding.repository_id), submission_id_ref=str(record.submission_id),
                snapshot_id_ref=str(snapshot.id), correlation_id=str(event.id))
    record = process_approval_outbox(event.id)
    if record is None:
        raise APIServiceError("approval_processing_pending", 503)
    return record
