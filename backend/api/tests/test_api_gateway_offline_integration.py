"""Offline integration through authenticated API, real runtime/graph/gateway, fake provider only."""

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from pydantic import BaseModel, ConfigDict

from agent_foundation.permissions import Permission
from apps.repositories.models import (Repository, RepositorySnapshot, RepositorySubmission, User,
                                      WorkflowExecution, Workspace, WorkspaceMembership)
from apps.repositories.providers.github import build_repository_manifest, extract_staged_archive
from apps.repositories.providers.github.snapshot_adapter import GitHubManifestSnapshotAdapter
from apps.repositories.services import create_workspace
from apps.repositories.snapshot_service import store_and_seal_snapshot
from apps.repositories.storage.local import LocalSnapshotStorage
from apps.repositories.test_github_manifest import make_archive, repository as github_repository, resolved_commit
from config.workflow_runtime import WorkflowRuntimeConfigurationError, build_api_workflow_runtime
from llm_gateway.contracts import GatewayRequest, LLMMessage
from llm_gateway.errors import (CrossPoolAccessDenied, InvalidProviderResponse, PoolConfigurationError)
from llm_gateway.pool_contracts import AGENT_POOL_MAP, PoolId
from llm_gateway.provider_registry import ProviderAdapterRegistry


class _SimpleStructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    value: str


class _OfflineProvider:
    """Provider boundary fake; no SDK, network transport, or paid route is used."""

    _verified_features = frozenset({"structured_output", "system_messages", "max_output_tokens"})

    def __init__(self):
        self.calls = []
        self.invalid_output = False

    def validate_route(self, route):
        declared = set(route.capabilities)
        if route.supports_structured_output:
            declared.add("structured_output")
        if route.model_id != "offline-integration-model" or not declared <= self._verified_features:
            raise ValueError("Offline test adapter rejected unverified route capabilities.")

    def complete(self, route, request, *, timeout_seconds, credential=None):
        from llm_gateway.contracts import ProviderResponse, Usage

        self.calls.append({"agent_id": request.agent_id, "pool_id": request.pool_id,
                           "model_id": route.model_id, "credential_id": credential.credential_id})
        if self.invalid_output:
            content = {"unexpected": "invalid structured output"}
        elif "executive_overview" in (request.output_schema or {}).get("properties", {}):
            content = {"executive_overview": "The fixture contains a deterministic application entry point.",
                       "domain_summary": "The application's business domain is not inferable from this small fixture.",
                       "uncertainties": ["The fixture does not establish the complete business domain."]}
        else:
            content = {"agent_id": request.agent_id, "value": "validated-offline"}
        return ProviderResponse(content=content,
            usage=Usage(input_tokens=12, output_tokens=8, availability="available"))


def _runtime_environment(*, omit_credential=None, capability_override=None, paid_pool=None):
    agents_by_pool = {
        PoolId.POOL_A: [agent for agent, pool in AGENT_POOL_MAP.items() if pool == PoolId.POOL_A],
        PoolId.POOL_B: [agent for agent, pool in AGENT_POOL_MAP.items() if pool == PoolId.POOL_B],
        PoolId.POOL_C: [agent for agent, pool in AGENT_POOL_MAP.items() if pool == PoolId.POOL_C],
    }
    all_agents = sorted(AGENT_POOL_MAP)
    environ = {
        "CODEWISE_LLM_POLICY_CONFIG": json.dumps({"enabled": True, "allowed_agents": all_agents,
            "allowed_models": [["groq", "offline-integration-model"]],
            "enabled_tiers": ["free"], "allow_paid": False}),
        "CODEWISE_LLM_MODEL_CAPABILITIES": json.dumps({"groq": {"offline-integration-model": [
            "structured_output", "system_messages", "max_output_tokens"]}, "gemini": {}}),
    }
    for pool_id, agents in agents_by_pool.items():
        suffix = pool_id.value[-1].upper()
        secret_name = f"CODEWISE_LLM_CREDENTIAL_TEST_{suffix}"
        if secret_name != omit_credential:
            environ[secret_name] = f"offline-fixture-secret-{suffix}"
        capabilities = ["system_messages", "max_output_tokens"]
        if capability_override and capability_override[0] == pool_id:
            capabilities.extend(capability_override[1])
        tier = "paid" if pool_id == paid_pool else "free"
        environ[f"CODEWISE_LLM_{pool_id.value.upper()}_CONFIG"] = json.dumps({
            "enabled": True,
            "paid_authorized": pool_id == paid_pool,
            "routes": [{"route": {"provider_id": "groq", "model_id": "offline-integration-model",
                "tier": tier, "supported_tasks": ["analysis"], "capabilities": capabilities,
                "supports_structured_output": True}, "credentials": [{
                    "credential_id": f"offline-pool-{suffix}", "secret_ref": secret_name,
                    "identity": {"provider_id": "groq", "account_id": f"offline-account-{suffix}",
                        "project_id": f"offline-project-{suffix}", "quota_bucket_id": f"offline-quota-{suffix}"}}]}],
            "limits": {"max_requests_per_minute": 60, "max_requests_per_execution": 20,
                "max_requests_per_agent_per_hour": 100, "max_input_bytes_per_request": 100_000,
                "max_output_tokens_per_request": 4000, "max_input_tokens_per_hour": 100_000,
                "max_output_tokens_per_hour": 100_000, "max_concurrent_requests": 4},
            "quota_limits": {"max_requests_per_minute": 60, "max_input_tokens_per_hour": 100_000,
                "max_output_tokens_per_hour": 100_000, "max_concurrent_requests": 4}})
    return environ


class APIGatewayOfflineIntegrationTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private_root = Path(self.temp.name) / "private-snapshots"
        self.user = User.objects.create_user(username="offline-api-owner", password="offline-test-password")
        self.workspace = create_workspace(owner=self.user, name="Offline gateway integration")
        identity = github_repository()
        self.repository = Repository.objects.create(workspace=self.workspace, provider="github",
            provider_repository_id=identity.provider_repository_id, owner=identity.owner, name=identity.name,
            canonical_url=identity.canonical_url, visibility="public", default_branch="main")
        self.snapshot = self._create_real_sealed_snapshot(identity)
        self.submission = RepositorySubmission.objects.create(workspace=self.workspace, submitted_by=self.user,
            submitted_url=identity.canonical_url, normalized_locator=f"{identity.owner}/{identity.name}",
            repository=self.repository, snapshot=self.snapshot, status="sealed", idempotency_key="offline-submission",
            request_fingerprint=hashlib.sha256(b"offline-submission").hexdigest())
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_real_sealed_snapshot(self, identity):
        source = {"src/main.py": b"def main():\n    return 'offline fixture'\n"}
        archive = make_archive(Path(self.temp.name), source)
        extraction_context = extract_staged_archive(archive, staging_root=self.temp.name)
        resolved = resolved_commit(repo=identity)
        with extraction_context as extracted:
            manifest = build_repository_manifest(resolved_commit=resolved, staged_archive=archive,
                                                  extracted_archive=extracted)
            snapshot = RepositorySnapshot.objects.create(workspace=self.workspace, repository=self.repository,
                commit_sha=resolved.commit_sha, visibility_at_capture="public",
                manifest_schema_version=manifest.schema_version)
            store_and_seal_snapshot(user=self.user, workspace_id=self.workspace.id, snapshot_id=snapshot.id,
                resolved_commit=resolved, staged_archive=archive, extracted_archive=extracted,
                manifest=manifest, manifest_codec=GitHubManifestSnapshotAdapter(),
                storage=LocalSnapshotStorage(self.private_root))
        snapshot.refresh_from_db()
        return snapshot

    def _execute_with_fake_provider(self, provider, environ, *, key="offline-run"):
        registry = ProviderAdapterRegistry({"groq": provider, "gemini": provider})
        factory_path = "config.workflow_runtime.build_api_workflow_runtime"
        build_api_workflow_runtime.cache_clear()
        try:
            with patch.dict(os.environ, environ, clear=True), \
                 patch("config.workflow_runtime.ProviderAdapterRegistry.groq_and_gemini", return_value=registry), \
                 override_settings(CODEWISE_API_WORKFLOW_FACTORY=factory_path,
                                  CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=False,
                                  REPOSITORY_PRIVATE_STORAGE_ROOT=self.private_root):
                response = self.client.post(
                    f"/api/v1/workspaces/{self.workspace.id}/executions/",
                    {"submission_id": str(self.submission.id), "groups": [], "approval_required": False},
                    format="json", HTTP_IDEMPOTENCY_KEY=key)
                return response, build_api_workflow_runtime()
        finally:
            build_api_workflow_runtime.cache_clear()

    def test_authenticated_api_runtime_graph_agent_bindings_gateway_pools_and_persisted_result(self):
        provider = _OfflineProvider()
        environ = _runtime_environment()
        # Keep isolated fixture credentials available to the real runtime resolver
        # while the test exercises its bound Agent 4 and Agent 7 models.
        self.enterContext(patch.dict(os.environ, environ, clear=True))

        # Authentication is checked before runtime/provider work.
        anonymous = APIClient().post(f"/api/v1/workspaces/{self.workspace.id}/executions/",
            {"submission_id": str(self.submission.id), "groups": []}, format="json",
            HTTP_IDEMPOTENCY_KEY="anonymous-run")
        self.assertIn(anonymous.status_code, (401, 403))
        self.assertEqual(provider.calls, [])

        response, runtime = self._execute_with_fake_provider(provider, environ)
        self.assertEqual(response.status_code, 202, response.data)
        execution = WorkflowExecution.objects.get(pk=response.data["execution"]["id"])
        self.assertEqual([(item["agent_id"], item["pool_id"]) for item in provider.calls],
                         [("business_logic_analyst", "pool_a")])
        agent2 = next(row for row in execution.result["agents"] if row["agent_id"] == "business_logic_analyst")
        self.assertEqual(agent2["status"], "completed")
        self.assertEqual(agent2["output"]["llm_status"], "completed")
        self.assertEqual(agent2["output"]["execution_id"], str(uuid.uuid5(execution.id, "business_logic_analyst")))
        self.assertEqual(agent2["output"]["snapshot_id"], str(self.snapshot.id))
        self.assertEqual(execution.result["execution_id"], str(execution.id))

        # Exercise the same production model-binding helper for Pool B and C.
        from config.workflow_runtime import create_gateway_chat_model
        request = SimpleNamespace(execution_id=execution.id, correlation_id="binding-test", trace_id="binding-test")
        for agent_id in ("security_analyst", "test_engineer"):
            with self.subTest(agent_id=agent_id):
                context = SimpleNamespace(agent_id=agent_id, execution_id=execution.id,
                    permissions=frozenset({Permission.LLM_INVOKE}))
                definition = SimpleNamespace(contract=SimpleNamespace(agent_id=agent_id))
                model = create_gateway_chat_model(gateway=runtime.gateway, definition=definition,
                    request=request, context=context)
                parsed = model.with_structured_output(_SimpleStructuredResult).invoke("offline pool check")
                self.assertEqual(parsed.agent_id, agent_id)

        calls = [(item["agent_id"], item["pool_id"]) for item in provider.calls]
        self.assertEqual(calls, [("business_logic_analyst", "pool_a"),
                                 ("security_analyst", "pool_b"), ("test_engineer", "pool_c")])
        self.assertNotIn("structure_analyzer", [item["agent_id"] for item in provider.calls])

        before = len(provider.calls)
        with self.assertRaises(WorkflowRuntimeConfigurationError):
            runtime = build_api_workflow_runtime()
            create_gateway_chat_model(gateway=runtime.gateway,
                definition=SimpleNamespace(contract=SimpleNamespace(agent_id="structure_analyzer")),
                request=request, context=SimpleNamespace(agent_id="structure_analyzer", execution_id=execution.id,
                    permissions=frozenset({Permission.LLM_INVOKE})))
        self.assertEqual(len(provider.calls), before)

        # The agent cannot override its immutable assignment in a raw gateway request.
        with self.assertRaises(CrossPoolAccessDenied):
            runtime.gateway.invoke(GatewayRequest(agent_id="business_logic_analyst", pool_id="pool_b",
                execution_id=str(execution.id), messages=(LLMMessage(role="user", content="mismatch"),),
                correlation_id="mismatch", trace_id="mismatch"), permissions={Permission.LLM_INVOKE})
        self.assertEqual(len(provider.calls), before)

    def test_missing_credentials_invalid_capabilities_and_paid_routes_fail_before_provider_call(self):
        provider = _OfflineProvider()
        registry = ProviderAdapterRegistry({"groq": provider, "gemini": provider})
        from config.workflow_runtime import build_gateway_from_environment, load_runtime_configuration

        missing = _runtime_environment(omit_credential="CODEWISE_LLM_CREDENTIAL_TEST_B")
        with patch("config.workflow_runtime.ProviderAdapterRegistry.groq_and_gemini", return_value=registry):
            with self.assertRaises(PoolConfigurationError) as caught:
                build_gateway_from_environment(missing)
            self.assertNotIn("offline-fixture-secret", str(caught.exception))

            invalid_capability = _runtime_environment(
                capability_override=(PoolId.POOL_B, ["temperature"]))
            with self.assertRaises(WorkflowRuntimeConfigurationError):
                load_runtime_configuration(invalid_capability)

            paid = _runtime_environment(paid_pool=PoolId.POOL_C)
            with self.assertRaises(WorkflowRuntimeConfigurationError):
                load_runtime_configuration(paid)

        self.assertEqual(provider.calls, [])

    def test_invalid_structured_provider_response_is_rejected_by_gateway(self):
        provider = _OfflineProvider()
        environ = _runtime_environment()
        # First create the real runtime through its normal factory and isolated fake registry.
        registry = ProviderAdapterRegistry({"groq": provider, "gemini": provider})
        build_api_workflow_runtime.cache_clear()
        try:
            with patch.dict(os.environ, environ, clear=True), \
                 patch("config.workflow_runtime.ProviderAdapterRegistry.groq_and_gemini", return_value=registry), \
                 override_settings(CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=False,
                                  REPOSITORY_PRIVATE_STORAGE_ROOT=self.private_root):
                runtime = build_api_workflow_runtime()
                execution_id = uuid.uuid4()
                agent_id = "business_logic_analyst"
                gateway = runtime.gateway
                provider.invalid_output = True
                request = GatewayRequest(agent_id=agent_id, pool_id="pool_a", execution_id=str(execution_id),
                    messages=(LLMMessage(role="user", content="must be structured"),),
                    output_schema=_SimpleStructuredResult.model_json_schema(),
                    correlation_id="invalid-structured", trace_id="invalid-structured")
                with self.assertRaises(InvalidProviderResponse):
                    gateway.invoke(request, permissions={Permission.LLM_INVOKE})
                self.assertEqual(len(provider.calls), 1)
        finally:
            build_api_workflow_runtime.cache_clear()

