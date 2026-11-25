import hashlib
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.repositories.models import (ExecutionSnapshotBinding, Repository, RepositoryGrant, RepositorySnapshot,
    RepositorySubmission, User, WorkflowExecution, Workspace, WorkspaceMembership)
from django.utils import timezone


class OfflineWorkflowRuntime:
    durable_approval_supported = False

    def __init__(self):
        self.calls = 0

    def workflow_for(self, *, execution, snapshot):
        self.calls += 1
        return OfflineWorkflow()


class OfflineWorkflow:
    has_checkpointer = False
    durable_checkpointer = False
    can_produce_patch = False
    can_approve = False

    @staticmethod
    def invoke(state, *, config=None):
        return {**state, "statuses": {agent: "unavailable" for agent in (
            "structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
            "migration_planner", "patch_author", "test_engineer", "review_agent")},
            "agent_results": {}, "errors": {}, "workflow_status": "partial", "approval": "not_applicable"}


class WorkflowAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="api-user", password="pass")
        self.other = User.objects.create_user(username="other-user", password="pass")
        self.workspace = Workspace.objects.create(name="Primary")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=self.user, role="owner")
        self.foreign = Workspace.objects.create(name="Foreign")
        WorkspaceMembership.objects.create(workspace=self.foreign, user=self.other, role="owner")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.runtime = OfflineWorkflowRuntime()
        self.override = override_settings(CODEWISE_API_WORKFLOW_FACTORY=lambda: self.runtime)
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def _sealed_snapshot(self):
        repository = Repository.objects.create(workspace=self.workspace, provider="github",
            provider_repository_id="100", owner="octo", name="repo", canonical_url="https://github.com/octo/repo",
            visibility="public", default_branch="main")
        snapshot = RepositorySnapshot(workspace=self.workspace, repository=repository, commit_sha="a" * 40,
            visibility_at_capture="public", status="sealed", manifest_schema_version="repository-manifest.v1",
            manifest_digest="b" * 64, manifest_storage_ref="manifest", content_digest="c" * 64,
            storage_ref="snapshot", sealed_at=__import__("django.utils.timezone", fromlist=["now"]).now())
        snapshot._allow_snapshot_seal = True
        snapshot.save()
        return repository, snapshot

    def _submission(self):
        repository, snapshot = self._sealed_snapshot()
        submission = RepositorySubmission.objects.create(workspace=self.workspace, submitted_by=self.user,
            submitted_url="https://github.com/octo/repo", normalized_locator="octo/repo", repository=repository,
            snapshot=snapshot, status="sealed", idempotency_key="submit-1",
            request_fingerprint=hashlib.sha256(b"submission").hexdigest())
        return repository, snapshot, submission

    def test_protected_endpoints_require_authentication(self):
        client = APIClient()
        response = client.get(f"/api/v1/workspaces/{self.workspace.id}/executions/")
        self.assertEqual(response.status_code, 403)

    def test_invalid_repository_url_is_rejected_without_network_access(self):
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/submissions/",
            {"repository_url": "http://127.0.0.1/private"}, format="json",
            HTTP_IDEMPOTENCY_KEY="bad-url")
        self.assertEqual(response.status_code, 400)

    def test_repository_submission_is_idempotent_and_snapshot_bound(self):
        repository, snapshot = self._sealed_snapshot()

        def offline_submit(*, submission, **kwargs):
            submission.repository = repository
            submission.snapshot = snapshot
            submission.status = RepositorySubmission.Status.SEALED
            submission.save(update_fields=("repository", "snapshot", "status", "updated_at"))
            return snapshot

        payload = {"repository_url": "https://github.com/octo/repo", "ref": "main", "ref_kind": "branch"}
        with patch("api.v1.services._public_submission", side_effect=offline_submit):
            first = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/submissions/", payload,
                format="json", HTTP_IDEMPOTENCY_KEY="same-submit")
            second = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/submissions/", payload,
                format="json", HTTP_IDEMPOTENCY_KEY="same-submit")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["submission"]["snapshot_id"], str(snapshot.id))
        self.assertEqual(RepositorySubmission.objects.filter(workspace=self.workspace).count(), 1)
        conflict = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/submissions/",
            {**payload, "ref": "release"}, format="json", HTTP_IDEMPOTENCY_KEY="same-submit")
        self.assertEqual(conflict.status_code, 409)

    def test_cross_workspace_submission_access_is_denied(self):
        response = self.client.get(f"/api/v1/workspaces/{self.foreign.id}/submissions/{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 403)

    def test_execution_start_is_deduplicated_and_partial_results_are_not_success(self):
        _, _, submission = self._submission()
        payload = {"submission_id": str(submission.id), "groups": [], "approval_required": False}
        url = f"/api/v1/workspaces/{self.workspace.id}/executions/"
        first = self.client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="run-1")
        second = self.client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="run-1")
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["execution"]["status"], "partial")
        self.assertEqual(first.data["execution"]["selected_agents"], ["structure_analyzer", "business_logic_analyst"])
        self.assertEqual(WorkflowExecution.objects.filter(workspace=self.workspace).count(), 1)
        self.assertEqual(self.runtime.calls, 1)
        record = WorkflowExecution.objects.get(workspace=self.workspace)
        self.assertEqual(set(record.selected_agents), {"structure_analyzer", "business_logic_analyst"})

    def test_execution_idempotency_key_conflict_and_unknown_group_are_rejected(self):
        _, _, submission = self._submission()
        url = f"/api/v1/workspaces/{self.workspace.id}/executions/"
        initial = {"submission_id": str(submission.id), "groups": [], "approval_required": False}
        self.client.post(url, initial, format="json", HTTP_IDEMPOTENCY_KEY="conflict-run")
        conflict = self.client.post(url, {**initial, "groups": ["patch_engineering"]},
            format="json", HTTP_IDEMPOTENCY_KEY="conflict-run")
        self.assertEqual(conflict.status_code, 409)
        invalid = self.client.post(url, {**initial, "groups": ["unknown"]},
            format="json", HTTP_IDEMPOTENCY_KEY="invalid-group")
        self.assertEqual(invalid.status_code, 400)

    def test_optional_group_matrix_persists_selection_and_keeps_mandatory_agents(self):
        _, _, submission = self._submission()
        optional = ("code_quality_security_planning", "patch_engineering", "final_review")
        agent_by_group = {
            optional[0]: {"code_quality_analyst", "security_analyst", "migration_planner"},
            optional[1]: {"patch_author", "test_engineer"},
            optional[2]: {"review_agent"},
        }
        url = f"/api/v1/workspaces/{self.workspace.id}/executions/"
        for mask in range(8):
            with self.subTest(groups=mask):
                groups = [group for index, group in enumerate(optional) if mask & (1 << index)]
                response = self.client.post(url, {"submission_id": str(submission.id), "groups": groups,
                    "approval_required": False}, format="json", HTTP_IDEMPOTENCY_KEY=f"matrix-{mask}")
                self.assertEqual(response.status_code, 202, response.data)
                selected = set(response.data["execution"]["selected_agents"])
                expected = {"structure_analyzer", "business_logic_analyst"}
                for group in groups:
                    expected.update(agent_by_group[group])
                self.assertEqual(selected, expected)
                record = WorkflowExecution.objects.get(pk=response.data["execution"]["id"])
                self.assertEqual(set(record.selected_agents), expected)
                self.assertTrue({"structure_analyzer", "business_logic_analyst"} <= selected)

    def test_approval_requires_both_optional_patch_and_review_agents_selected(self):
        _, _, submission = self._submission()
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/",
            {"submission_id": str(submission.id), "groups": [], "approval_required": True},
            format="json", HTTP_IDEMPOTENCY_KEY="approval-without-review")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "approval_agents_not_selected")

    def test_workspace_scoped_execution_and_result_reads(self):
        _, _, submission = self._submission()
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/",
            {"submission_id": str(submission.id), "groups": [], "approval_required": False},
            format="json", HTTP_IDEMPOTENCY_KEY="run-read")
        self.assertEqual(response.status_code, 202, response.data)
        execution_id = response.data["execution"]["id"]
        foreign = self.client.get(f"/api/v1/workspaces/{self.foreign.id}/executions/{execution_id}/agents/")
        self.assertEqual(foreign.status_code, 403)
        result = self.client.get(f"/api/v1/workspaces/{self.workspace.id}/executions/{execution_id}/agents/")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.data["agents"]), 8)

    def test_expired_private_repository_grant_blocks_execution(self):
        repository = Repository.objects.create(workspace=self.workspace, provider="github",
            provider_repository_id="200", owner="octo", name="private", canonical_url="https://github.com/octo/private",
            visibility="private", default_branch="main")
        grant = RepositoryGrant.objects.create(workspace=self.workspace, repository=repository, actor=self.user,
            permissions=["contents_read"], status="verified_read", checked_at=timezone.now(),
            expires_at=timezone.now() - timedelta(seconds=1))
        snapshot = RepositorySnapshot(workspace=self.workspace, repository=repository, source_grant=grant,
            commit_sha="d" * 40, visibility_at_capture="private", status="sealed",
            manifest_schema_version="repository-manifest.v1", manifest_digest="e" * 64,
            manifest_storage_ref="manifest-private", content_digest="f" * 64,
            storage_ref="snapshot-private", sealed_at=timezone.now())
        snapshot._allow_snapshot_seal = True
        snapshot.save()
        submission = RepositorySubmission.objects.create(workspace=self.workspace, submitted_by=self.user,
            submitted_url="https://github.com/octo/private", normalized_locator="octo/private", repository=repository,
            snapshot=snapshot, status="sealed", idempotency_key="private-submission",
            request_fingerprint=hashlib.sha256(b"private").hexdigest())
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/",
            {"submission_id": str(submission.id), "groups": [], "approval_required": False},
            format="json", HTTP_IDEMPOTENCY_KEY="private-run")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"], "snapshot_access_unavailable")

    def test_authorized_approval_fails_closed_on_invalid_stored_review(self):
        repository, snapshot, submission = self._submission()
        binding = ExecutionSnapshotBinding.objects.create(execution_id=uuid.uuid4(), workspace=self.workspace,
            repository=repository, snapshot=snapshot, commit_sha=snapshot.commit_sha,
            agent_ids=["structure_analyzer", "business_logic_analyst", "patch_author", "review_agent"],
            agent_group_ids=["patch_engineering", "final_review"])
        execution = WorkflowExecution.objects.create(id=binding.execution_id, workspace=self.workspace,
            submission=submission, binding=binding, requested_by=self.user, status="pending_approval",
            selected_groups=["patch_engineering", "final_review"], selected_agents=binding.agent_ids,
            result={"execution_id": str(binding.execution_id), "approval": "pending", "agents": []},
            idempotency_key="invalid-approval-run", request_fingerprint=hashlib.sha256(b"run-invalid").hexdigest())
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/{execution.id}/approval/",
            {"decision": "rejected", "proposal_ids": ["patch_0123456789abcdef"]}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(execution.approval_decisions.exists())

    def test_task_8a_unavailable_incomplete_review_cannot_pass_api_approval_boundary(self):
        from orchestration.contracts import WorkflowRequest
        from orchestration.graph import _as_workflow_result
        from orchestration.tests.test_workflow import WorkflowTests

        repository, snapshot, submission = self._submission()
        execution_id = uuid.uuid4()
        helper = WorkflowTests()
        helper.request = WorkflowRequest(execution_id=execution_id, workspace_id=self.workspace.id,
            repository_id=repository.id, snapshot_id=snapshot.id, commit_sha=snapshot.commit_sha,
            manifest_digest=snapshot.manifest_digest, correlation_id="api-audit", trace_id="api-audit")
        state = helper._approval_state(review_status="incomplete", llm_status="unavailable",
                                       review_lifecycle="completed")
        state["workflow_status"] = "partial"
        persisted_result = _as_workflow_result(helper.request, state).model_dump(mode="json")
        persisted_result["approval"] = "pending"
        binding = ExecutionSnapshotBinding.objects.create(execution_id=execution_id, workspace=self.workspace,
            repository=repository, snapshot=snapshot, commit_sha=snapshot.commit_sha,
            agent_ids=["structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                "security_analyst", "migration_planner", "patch_author", "test_engineer", "review_agent"],
            agent_group_ids=["code_quality_security_planning", "patch_engineering", "final_review"])
        execution = WorkflowExecution.objects.create(id=execution_id, workspace=self.workspace,
            submission=submission, binding=binding, requested_by=self.user, status="pending_approval",
            selected_groups=list(binding.agent_group_ids), selected_agents=list(binding.agent_ids),
            result=persisted_result, idempotency_key="api-audit-run",
            request_fingerprint=hashlib.sha256(b"api-audit").hexdigest())
        proposal_id = persisted_result["agents"][5]["output"]["proposals"][0]["proposal_id"]
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/{execution.id}/approval/",
            {"decision": "approved", "proposal_ids": [proposal_id]}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"], "approval_review_not_ready")
        self.assertFalse(execution.approval_decisions.exists())

    def test_approval_requires_workspace_admin_and_durable_checkpoint(self):
        repository, snapshot, submission = self._submission()
        binding = ExecutionSnapshotBinding.objects.create(execution_id=uuid.uuid4(), workspace=self.workspace,
            repository=repository, snapshot=snapshot, commit_sha=snapshot.commit_sha,
            agent_ids=["structure_analyzer", "business_logic_analyst", "patch_author", "review_agent"],
            agent_group_ids=["patch_engineering", "final_review"])
        execution = WorkflowExecution.objects.create(workspace=self.workspace, submission=submission, binding=binding,
            requested_by=self.user, status="pending_approval", selected_groups=["patch_engineering", "final_review"],
            selected_agents=binding.agent_ids, result={"execution_id": str(binding.execution_id), "approval": "pending"},
            idempotency_key="approval-run", request_fingerprint=hashlib.sha256(b"run").hexdigest())
        member = User.objects.create_user(username="member", password="pass")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=member, role="member")
        self.client.force_authenticate(member)
        response = self.client.post(f"/api/v1/workspaces/{self.workspace.id}/executions/{execution.id}/approval/",
            {"decision": "approved", "proposal_ids": ["patch_0123456789abcdef"]}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(execution.approval_decisions.exists())

