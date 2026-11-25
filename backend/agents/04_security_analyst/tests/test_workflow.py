import hashlib
import uuid

from django.test import SimpleTestCase
from pydantic import SecretStr

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from agent_foundation.context import ExecutionContext
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import (business_logic_analyst_binding, register_available_agents,
    security_analyst_binding, structure_analyzer_binding)
from llm_gateway.contracts import ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.credentials import ResolvedCredential
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import (AGENT_POOL_MAP, CredentialReference, PoolConfiguration,
    PoolConfigurations, PoolId, PoolLimits, PoolRoute, QuotaIdentity, QuotaLimits)
from orchestration.contracts import WorkflowRequest
from orchestration.graph import build_workflow, run_workflow


class SnapshotReader:
    def __init__(self):
        source = ("def handler(request, cursor):\n"
                  "    value = request.GET['name']\n"
                  "    cursor.execute(f\"SELECT * FROM users WHERE name='{value}'\")\n")
        raw = source.encode("utf-8")
        self.files = {"app/views.py": raw}
        self.records = {path: {"path": path, "size_bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest()} for path, value in self.files.items()}

    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class CapturingProvider:
    def __init__(self):
        self.calls = []

    def complete(self, route, request, *, timeout_seconds=60.0, credential=None):
        self.calls.append((request.agent_id, credential.credential_id))
        if request.agent_id == "business_logic_analyst":
            output = {"executive_overview": "The bounded source represents a request handler.",
                      "domain_summary": "Insufficient evidence for deeper business context."}
        elif request.agent_id == "security_analyst":
            output = {"overview": "Request-derived data reaches a dynamic SQL argument; parameterize the query."}
        else:
            output = {"overview": "Synthetic quality interpretation."}
        return ProviderResponse(content=output, usage=Usage(input_tokens=20, output_tokens=12))


class Resolver:
    def resolve(self, reference):
        return ResolvedCredential(reference.credential_id, SecretStr("in-memory-test-credential"))


def _pool(pool_id, credential_id, account_id):
    route = ModelRoute(provider_id="synthetic", model_id="test-free", tier=ModelTier.FREE,
        supported_tasks={"analysis"}, supports_structured_output=True)
    reference = CredentialReference(credential_id=credential_id,
        secret_ref=f"CODEWISE_LLM_CREDENTIAL_{credential_id.upper().replace('-', '_')}",
        identity=QuotaIdentity(provider_id="synthetic", account_id=account_id,
            project_id=account_id, quota_bucket_id=account_id))
    return PoolConfiguration(pool_id=pool_id, enabled=True,
        routes=(PoolRoute(route=route, credentials=(reference,)),),
        limits=PoolLimits(max_requests_per_minute=40, max_requests_per_execution=8,
            max_requests_per_agent_per_hour=100, max_input_bytes_per_request=200_000,
            max_output_tokens_per_request=5000, max_input_tokens_per_hour=500_000,
            max_output_tokens_per_hour=500_000, max_concurrent_requests=4),
        quota_limits=QuotaLimits(max_requests_per_minute=40, max_input_tokens_per_hour=500_000,
            max_output_tokens_per_hour=500_000, max_concurrent_requests=4))


class SecurityAnalystWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.reader = SnapshotReader()
        self.request = WorkflowRequest(execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(), commit_sha="a" * 40,
            manifest_digest="b" * 64, correlation_id="agent4-test", trace_id="agent4-trace",
            agents=frozenset({"security_analyst"}))

    def _run(self, *, selected=True, deny_agent2_llm=False):
        registry = AgentRegistry()
        register_available_agents(registry, self.reader)
        provider = CapturingProvider()
        pools = PoolConfigurations(pools={
            PoolId.POOL_A: _pool(PoolId.POOL_A, "pool-a-primary", "account-a"),
            PoolId.POOL_B: _pool(PoolId.POOL_B, "pool-b-primary", "account-b"),
            PoolId.POOL_C: PoolConfiguration(pool_id=PoolId.POOL_C),
        })
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
            providers={"synthetic": provider}, pool_configurations=pools,
            credential_resolver=Resolver())
        order = []

        def context_factory(workflow_request, agent_id, execution_id):
            permissions = {Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ}
            if agent_id != "structure_analyzer" and not (deny_agent2_llm and agent_id == "business_logic_analyst"):
                permissions.add(Permission.LLM_INVOKE)
            return ExecutionContext.make(principal_id="synthetic", workspace_id=workflow_request.workspace_id,
                repository_id=workflow_request.repository_id, snapshot_id=workflow_request.snapshot_id,
                commit_sha=workflow_request.commit_sha, manifest_digest=workflow_request.manifest_digest,
                execution_id=execution_id, agent_id=agent_id, permissions=frozenset(permissions),
                correlation_id=workflow_request.correlation_id, trace_id=workflow_request.trace_id)

        def model_factory(definition, request, context):
            return GatewayChatModel(gateway=gateway, permissions=context.permissions,
                agent_id=definition.contract.agent_id, execution_id=str(request.execution_id),
                correlation_id=request.correlation_id, trace_id=request.trace_id)

        bindings = {"structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "security_analyst": security_analyst_binding(context_factory)}
        workflow = build_workflow(registry, ExecutionLifecycle(registry, chat_model_factory=model_factory),
            bindings, observer=lambda event: order.append(event["agent_id"]))
        request = self.request if selected else self.request.model_copy(update={"agents": frozenset()})
        result = run_workflow(workflow, request)
        return result, provider, order

    def test_agent4_runs_after_mandatory_agents_uses_pool_b_and_reasoned_evidence(self):
        result, provider, order = self._run()
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(AGENT_POOL_MAP["security_analyst"], PoolId.POOL_B)
        self.assertEqual(agents["structure_analyzer"].status, "completed")
        self.assertEqual(agents["business_logic_analyst"].status, "completed")
        self.assertEqual(agents["security_analyst"].status, "completed", agents["security_analyst"].error_codes)
        self.assertEqual(order[:3], ["structure_analyzer", "business_logic_analyst", "security_analyst"])
        self.assertIn(("business_logic_analyst", "pool-a-primary"), provider.calls)
        self.assertIn(("security_analyst", "pool-b-primary"), provider.calls)
        self.assertNotIn(("security_analyst", "pool-a-primary"), provider.calls)
        output = agents["security_analyst"].output
        self.assertEqual(output["llm_status"], "completed")
        self.assertTrue(any(item["classification"] == "sql_injection_candidate"
                            for item in output["findings"]))

    def test_agent4_is_skipped_when_not_selected(self):
        result, provider, _ = self._run(selected=False)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["security_analyst"].status, "skipped")
        self.assertNotIn("security_analyst", {agent for agent, _credential in provider.calls})

    def test_agent2_dependency_failure_skips_agent4_without_pool_b_invocation(self):
        result, provider, _ = self._run(deny_agent2_llm=True)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["business_logic_analyst"].status, "failed")
        self.assertEqual(agents["security_analyst"].status, "skipped")
        self.assertIn("dependency_incomplete", agents["security_analyst"].error_codes)
        self.assertNotIn("security_analyst", {agent for agent, _credential in provider.calls})
