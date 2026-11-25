"""End-to-end approval outbox tests requiring an explicit disposable PostgreSQL DB.

Set CODEWISE_TEST_POSTGRESQL_URL to a disposable checkpoint database with a name
containing 'test', and configure Django's test database as PostgreSQL. These tests
never fall back to SQLite and make no provider calls.
"""

import hashlib
import importlib.util
import os
import threading
import unittest
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.repositories.models import (ApprovalDecision, ApprovalOutboxEvent,
    AuditEvent, ExecutionSnapshotBinding, Repository, RepositorySnapshot,
    RepositorySubmission, User, WorkflowExecution, Workspace, WorkspaceMembership)
from api.v1.services import APIServiceError, approve_execution
from orchestration.contracts import WorkflowRequest
from orchestration.graph import WorkflowGraph, _as_workflow_result, _finalize, _approval_node


_DSN = os.environ.get("CODEWISE_TEST_POSTGRESQL_URL", "")
_HAS_PG = all(importlib.util.find_spec(name) for name in
               ("psycopg", "psycopg_pool", "langgraph.checkpoint.postgres"))
_SKIP = ("Requires CODEWISE_TEST_POSTGRESQL_URL for a disposable PostgreSQL database named with 'test', "
         "PostgreSQL Django test settings, and installed Psycopg/LangGraph PostgreSQL dependencies.")


class PostgreSQLApprovalOutboxTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        parsed = urlparse(_DSN)
        if not _DSN or not _HAS_PG:
            raise unittest.SkipTest(_SKIP)
        if parsed.scheme not in {"postgres", "postgresql"} or "test" not in parsed.path.lower():
            raise unittest.SkipTest("CODEWISE_TEST_POSTGRESQL_URL must target an explicitly disposable PostgreSQL database named with 'test'.")
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("Approval outbox integration requires Django's test database to use PostgreSQL; SQLite fallback is forbidden.")
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        cls.pool = ConnectionPool(conninfo=_DSN,
            kwargs={"autocommit": True, "row_factory": dict_row}, min_size=1, max_size=6)
        cls.pool.wait(timeout=5)
        cls.saver = PostgresSaver(cls.pool, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        cls.saver.setup()
        cls.thread_ids = set()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "pool"):
            for thread_id in cls.thread_ids:
                with cls.pool.connection() as conn:
                    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))
            cls.pool.close()
        super().tearDownClass()

    def setUp(self):
        self.actor = User.objects.create_user(username=f"owner-{uuid.uuid4()}", password="unused")
        self.workspace = Workspace.objects.create(name="Approval integration")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.actor, role="owner")
        self.repository = Repository.objects.create(workspace=self.workspace, provider="github",
            provider_repository_id=str(uuid.uuid4()), owner="octo", name="repo",
            canonical_url="https://github.com/octo/repo", visibility="public", default_branch="main")
        self.snapshot = RepositorySnapshot(workspace=self.workspace, repository=self.repository,
            commit_sha="a" * 40, visibility_at_capture="public", status="sealed",
            manifest_schema_version="repository-manifest.v1", manifest_digest="b" * 64,
            manifest_storage_ref="manifest", content_digest="c" * 64,
            storage_ref="snapshot", sealed_at=timezone.now())
        self.snapshot._allow_snapshot_seal = True
        self.snapshot.save()
        self.submission = RepositorySubmission.objects.create(workspace=self.workspace,
            submitted_by=self.actor, submitted_url="https://github.com/octo/repo",
            normalized_locator="octo/repo", repository=self.repository, snapshot=self.snapshot,
            status="sealed", idempotency_key=str(uuid.uuid4()),
            request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        self.execution_id = uuid.uuid4()
        selected = ["structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                    "security_analyst", "migration_planner", "patch_author", "test_engineer", "review_agent"]
        self.binding = ExecutionSnapshotBinding.objects.create(execution_id=self.execution_id,
            workspace=self.workspace, repository=self.repository, snapshot=self.snapshot,
            commit_sha=self.snapshot.commit_sha, agent_ids=selected,
            agent_group_ids=["code_quality_security_planning", "patch_engineering", "final_review"])
        self.execution = WorkflowExecution.objects.create(id=self.execution_id,
            workspace=self.workspace, submission=self.submission, binding=self.binding,
            requested_by=self.actor, status="pending_approval", selected_groups=list(self.binding.agent_group_ids),
            selected_agents=selected, idempotency_key=str(uuid.uuid4()),
            request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        self.request, state = self._valid_approval_state()
        self.execution.result = _as_workflow_result(self.request, state).model_dump(mode="json")
        self.execution.result["approval"] = "pending"
        self.execution.save(update_fields=("result", "updated_at"))
        from langgraph.graph import END, START, StateGraph
        from orchestration.state import WorkflowState
        graph = StateGraph(WorkflowState)
        graph.add_node("approval_gate", _approval_node)
        graph.add_node("finalize", _finalize)
        graph.add_edge(START, "approval_gate")
        graph.add_edge("approval_gate", "finalize")
        graph.add_edge("finalize", END)
        self.compiled = graph.compile(checkpointer=self.saver)
        self.workflow = WorkflowGraph(self.compiled, has_checkpointer=True,
            can_produce_patch=True, can_approve=True, durable_checkpointer=True)
        self.thread_ids = getattr(type(self), "thread_ids", set())
        self.thread_ids.add(str(self.execution_id))
        paused = self.compiled.invoke(state, config=self._config(self.execution_id))
        self.assertTrue(paused.get("__interrupt__"))
        self.runtime = _PostgresRuntime(self.workflow)
        self.settings_override = override_settings(CODEWISE_API_WORKFLOW_FACTORY=lambda: self.runtime)
        self.settings_override.enable()
        self.payload = {"decision": "approved", "proposal_ids": ["patch_0123456789abcdef"]}

    def tearDown(self):
        self.settings_override.disable()

    @staticmethod
    def _config(execution_id):
        return {"configurable": {"thread_id": str(execution_id)}}

    def _valid_approval_state(self):
        from orchestration.tests.test_workflow import WorkflowTests
        request = WorkflowRequest(execution_id=self.execution_id, workspace_id=self.workspace.id,
            repository_id=self.repository.id, snapshot_id=self.snapshot.id,
            commit_sha=self.snapshot.commit_sha, manifest_digest=self.snapshot.manifest_digest,
            correlation_id="approval-test", trace_id="approval-test",
            groups=frozenset({"code_quality_security_planning", "patch_engineering", "final_review"}),
            approval_required=True)
        helper = WorkflowTests()
        helper.request = request
        state = helper._approval_state()
        output = state["agent_results"]["review_agent"]
        output["reviewed_agents"] = [dict(row, output_present=(row["agent_id"] == "patch_author"))
                                     for row in output["reviewed_agents"]]
        state["agent_results"]["review_agent"] = output
        for agent_id in ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                         "security_analyst", "migration_planner", "test_engineer"):
            state["agent_results"].pop(agent_id, None)
        return request, state

    def _approve(self, payload=None):
        return approve_execution(user=self.actor, workspace_id=self.workspace.id,
            execution_id=self.execution.id, payload=payload or self.payload)

    def test_success_duplicate_and_actor_audit_are_idempotent_without_applying_patch(self):
        result = self._approve()
        repeated = self._approve()
        event = ApprovalOutboxEvent.objects.get(execution=self.execution)
        decision = ApprovalDecision.objects.get(execution=self.execution)
        self.assertEqual(result.status, "completed")
        self.assertEqual(repeated.pk, result.pk)
        self.assertEqual(event.status, ApprovalOutboxEvent.Status.COMPLETED)
        self.assertEqual(decision.actor_id, self.actor.id)
        self.assertEqual(decision.snapshot_id, self.snapshot.id)
        self.assertEqual(AuditEvent.objects.filter(correlation_id=str(event.id),
            action="human_patch_approval_decision", actor=self.actor).count(), 1)
        self.assertTrue(all(not item["output"]["applied"] for item in
            self.execution.result["agents"][7]["output"].get("patch_reviews", [])))
        self.assertFalse(AuditEvent.objects.filter(action__icontains="publish",
            correlation_id=str(event.id)).exists())

    def test_failure_before_checkpoint_is_retryable_and_resumes_same_execution(self):
        from api.v1.approval_outbox import process_approval_outbox
        from orchestration.graph import resume_or_recover_workflow
        with patch("orchestration.graph.resume_or_recover_workflow", side_effect=RuntimeError("injected")):
            with self.assertRaises(APIServiceError):
                self._approve()
        event = ApprovalOutboxEvent.objects.get(execution=self.execution)
        self.assertEqual(event.status, ApprovalOutboxEvent.Status.RETRYABLE)
        self.assertTrue(self.compiled.get_state(self._config(self.execution.id)).tasks[0].interrupts)
        result = process_approval_outbox(event.id)
        self.assertEqual(result.status, "completed")

    def test_failure_after_checkpoint_and_django_rollback_reconciles_on_retry(self):
        from api.v1.approval_outbox import process_approval_outbox
        with patch("api.v1.approval_outbox._after_decision_persistence", side_effect=RuntimeError("injected")):
            with self.assertRaises(APIServiceError):
                self._approve()
        event = ApprovalOutboxEvent.objects.get(execution=self.execution)
        self.assertEqual(event.status, ApprovalOutboxEvent.Status.RETRYABLE)
        self.assertFalse(ApprovalDecision.objects.filter(execution=self.execution).exists())
        self.assertEqual(self.execution.status, WorkflowExecution.Status.PENDING_APPROVAL)
        self.assertFalse(AuditEvent.objects.filter(action="human_patch_approval_decision",
            correlation_id=str(event.id)).exists())
        self.assertEqual(self.compiled.get_state(self._config(self.execution.id)).values["approval"], "approved")
        result = process_approval_outbox(event.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(ApprovalDecision.objects.filter(execution=self.execution).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="human_patch_approval_decision",
            correlation_id=str(event.id)).count(), 1)

    def test_expired_processing_lease_recovers_interrupted_worker(self):
        from api.v1.approval_outbox import process_approval_outbox
        event = ApprovalOutboxEvent.objects.create(execution=self.execution, workspace=self.workspace,
            snapshot_id=self.snapshot.id, actor=self.actor, decision="approved",
            proposal_ids=self.payload["proposal_ids"], request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        ApprovalOutboxEvent.objects.filter(pk=event.pk).update(status=ApprovalOutboxEvent.Status.PROCESSING,
            attempts=1, lease_until=timezone.now() - timedelta(seconds=1))
        result = process_approval_outbox(event.id)
        event.refresh_from_db()
        self.assertEqual(result.status, "completed")
        self.assertEqual(event.status, ApprovalOutboxEvent.Status.COMPLETED)

    def test_unauthorized_actor_cannot_enqueue_approval(self):
        member = User.objects.create_user(username=f"member-{uuid.uuid4()}", password="unused")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=member, role="member")
        with self.assertRaises(APIServiceError) as caught:
            approve_execution(user=member, workspace_id=self.workspace.id,
                execution_id=self.execution.id, payload=self.payload)
        self.assertEqual(caught.exception.status, 403)
        self.assertFalse(ApprovalOutboxEvent.objects.filter(execution=self.execution).exists())

    def test_snapshot_identity_mismatch_is_rejected_before_outbox_creation(self):
        self.execution.result["execution_id"] = str(uuid.uuid4())
        self.execution.save(update_fields=("result", "updated_at"))
        with self.assertRaises(APIServiceError) as caught:
            self._approve()
        self.assertEqual(caught.exception.code, "execution_snapshot_identity_mismatch")
        self.assertFalse(ApprovalOutboxEvent.objects.filter(execution=self.execution).exists())

    def test_cross_execution_checkpoint_replay_fails_closed(self):
        from api.v1.approval_outbox import process_approval_outbox
        other_id = uuid.uuid4()
        current_execution_id = self.execution_id
        self.execution_id = other_id
        other_request, other_state = self._valid_approval_state()
        self.execution_id = current_execution_id
        other_result = _as_workflow_result(other_request, other_state).model_dump(mode="json")
        other_result["approval"] = "pending"
        other_binding = ExecutionSnapshotBinding.objects.create(execution_id=other_id,
            workspace=self.workspace, repository=self.repository, snapshot=self.snapshot,
            commit_sha=self.snapshot.commit_sha, agent_ids=list(self.execution.selected_agents),
            agent_group_ids=list(self.execution.selected_groups))
        other = WorkflowExecution.objects.create(id=other_id, workspace=self.workspace,
            submission=self.submission, binding=other_binding, requested_by=self.actor,
            status="pending_approval", selected_groups=list(self.execution.selected_groups),
            selected_agents=list(self.execution.selected_agents), result=other_result,
            idempotency_key=str(uuid.uuid4()), request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        event = ApprovalOutboxEvent.objects.create(execution=other, workspace=self.workspace,
            snapshot_id=self.snapshot.id, actor=self.actor, decision="approved",
            proposal_ids=self.payload["proposal_ids"], request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        self.thread_ids.add(str(other_id))
        self.assertIsNone(process_approval_outbox(event.id))
        event.refresh_from_db()
        self.assertEqual(event.status, ApprovalOutboxEvent.Status.FAILED)
        self.assertFalse(ApprovalDecision.objects.filter(execution=other).exists())
        self.assertEqual(self.compiled.get_state(self._config(self.execution.id)).values["execution_id"],
                         str(self.execution.id))

    def test_concurrent_duplicate_workers_claim_one_event(self):
        from api.v1.approval_outbox import _claim
        event = ApprovalOutboxEvent.objects.create(execution=self.execution, workspace=self.workspace,
            snapshot_id=self.snapshot.id, actor=self.actor, decision="approved",
            proposal_ids=self.payload["proposal_ids"], request_fingerprint=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        barrier = threading.Barrier(2)
        def claim():
            barrier.wait()
            return _claim(event.id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(value is not None for value in claims), 1)

    def test_concurrent_approval_requests_create_one_command_and_one_decision(self):
        barrier = threading.Barrier(2)
        def approve():
            barrier.wait()
            try:
                return self._approve().status
            except APIServiceError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: approve(), range(2)))
        self._approve()
        self.assertEqual(ApprovalOutboxEvent.objects.filter(execution=self.execution).count(), 1)
        self.assertEqual(ApprovalDecision.objects.filter(execution=self.execution).count(), 1)
        self.assertTrue(all(value in {"completed", "approval_processing_pending"} for value in outcomes))


class _PostgresRuntime:
    durable_approval_supported = True
    def __init__(self, workflow):
        self.workflow = workflow
    def workflow_for(self, *, execution, snapshot):
        return self.workflow

