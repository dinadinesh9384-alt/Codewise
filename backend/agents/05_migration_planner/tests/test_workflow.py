import hashlib
import uuid

from django.test import SimpleTestCase
from pydantic import SecretStr

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from agent_foundation.context import ExecutionContext
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import (business_logic_analyst_binding, code_quality_analyst_binding,
    migration_planner_binding, register_available_agents, security_analyst_binding,
    structure_analyzer_binding)
from llm_gateway.contracts import ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.credentials import ResolvedCredential
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import (CredentialReference, PoolConfiguration, PoolConfigurations,
    PoolId, PoolLimits, PoolRoute, QuotaIdentity, QuotaLimits, AGENT_POOL_MAP)
from orchestration.contracts import WorkflowRequest
from orchestration.graph import build_workflow, run_workflow


class Reader:
    def __init__(self):
        data = ("def handler(request):\n    value = request.GET.get('id')\n    return value\n")
        raw = data.encode()
        self.files = {"app.py": raw}
        self.records = {p: {"path": p, "size_bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                        for p, b in self.files.items()}
    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class Provider:
    def __init__(self, fail_agent=None): self.calls, self.fail_agent, self.planner_messages = [], fail_agent, None
    def complete(self, route, request, *, timeout_seconds=60.0, credential=None):
        self.calls.append((request.agent_id, credential.credential_id))
        if request.agent_id == "migration_planner": self.planner_messages = request.messages
        if request.agent_id == self.fail_agent:
            raise RuntimeError("synthetic upstream failure")
        if request.agent_id == "business_logic_analyst":
            value = {"executive_overview": "A request-derived value passes through the handler.",
                     "domain_summary": "Only the visible request flow is supported."}
        elif request.agent_id == "migration_planner":
            value = {"overview": "The repository has a small Python request handler.",
                     "database": "No database migration is evidenced.", "database_status": "not_detected",
                     "data_migration_required": "unknown", "api": "A route is not sufficiently evidenced as a contract.",
                     "api_status": "not_detected"}
        else:
            value = {"overview": "Bounded synthetic specialist interpretation."}
        return ProviderResponse(content=value, usage=Usage(input_tokens=20, output_tokens=12))


class Resolver:
    def resolve(self, reference): return ResolvedCredential(reference.credential_id, SecretStr("synthetic-only"))


def pool(pool_id, credential_id):
    if pool_id == PoolId.POOL_C: return PoolConfiguration(pool_id=pool_id)
    route = ModelRoute(provider_id="synthetic", model_id="free-test", tier=ModelTier.FREE,
        supported_tasks={"analysis"}, supports_structured_output=True)
    ref = CredentialReference(credential_id=credential_id, secret_ref=f"secret-ref:synthetic/{credential_id}",
        identity=QuotaIdentity(provider_id="synthetic", account_id=credential_id,
            project_id=credential_id, quota_bucket_id=credential_id))
    limits = PoolLimits(max_requests_per_minute=50, max_requests_per_execution=12,
        max_requests_per_agent_per_hour=100, max_input_bytes_per_request=200_000,
        max_output_tokens_per_request=8000, max_input_tokens_per_hour=500_000,
        max_output_tokens_per_hour=500_000, max_concurrent_requests=4)
    quotas = QuotaLimits(max_requests_per_minute=50, max_input_tokens_per_hour=500_000,
        max_output_tokens_per_hour=500_000, max_concurrent_requests=4)
    return PoolConfiguration(pool_id=pool_id, enabled=True,
        routes=(PoolRoute(route=route, credentials=(ref,)),), limits=limits, quota_limits=quotas)


class MigrationPlannerWorkflowTests(SimpleTestCase):
    def run_case(self, agents, *, no_quality_permission=False, fail_agent=None):
        reader, registry = Reader(), AgentRegistry()
        register_available_agents(registry, reader)
        provider = Provider(fail_agent)
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
            providers={"synthetic": provider}, pool_configurations=PoolConfigurations(pools={
                PoolId.POOL_A: pool(PoolId.POOL_A, "pool-a"), PoolId.POOL_B: pool(PoolId.POOL_B, "pool-b"),
                PoolId.POOL_C: pool(PoolId.POOL_C, "pool-c")} ), credential_resolver=Resolver())
        order = []
        def context_factory(req, agent_id, execution_id):
            permissions = {Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ}
            if agent_id != "structure_analyzer" and not (no_quality_permission and agent_id == "code_quality_analyst"):
                permissions.add(Permission.LLM_INVOKE)
            return ExecutionContext.make(principal_id="fake", workspace_id=req.workspace_id,
                repository_id=req.repository_id, snapshot_id=req.snapshot_id, commit_sha=req.commit_sha,
                manifest_digest=req.manifest_digest, execution_id=execution_id, agent_id=agent_id,
                permissions=frozenset(permissions), correlation_id=req.correlation_id, trace_id=req.trace_id)
        def model_factory(definition, execution, context):
            return GatewayChatModel(gateway=gateway, permissions=context.permissions,
                agent_id=definition.contract.agent_id, execution_id=str(execution.execution_id),
                correlation_id=execution.correlation_id, trace_id=execution.trace_id)
        bindings = {"structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "code_quality_analyst": code_quality_analyst_binding(context_factory),
            "security_analyst": security_analyst_binding(context_factory),
            "migration_planner": migration_planner_binding(context_factory)}
        workflow = build_workflow(registry, ExecutionLifecycle(registry, chat_model_factory=model_factory), bindings,
            observer=lambda event: order.append(event["agent_id"]))
        request = WorkflowRequest(execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(), commit_sha="a" * 40, manifest_digest="b" * 64,
            correlation_id="migration-graph-test", trace_id="migration-graph-test", agents=frozenset(agents))
        return run_workflow(workflow, request), provider, order

    def test_selected_planner_runs_after_selected_upstream_agents_via_pool_b(self):
        result, provider, order = self.run_case({"migration_planner", "code_quality_analyst", "security_analyst"})
        statuses = {item.agent_id: item.status for item in result.agents}
        self.assertEqual(AGENT_POOL_MAP["migration_planner"], PoolId.POOL_B)
        self.assertEqual(statuses["structure_analyzer"], "completed")
        self.assertEqual(statuses["business_logic_analyst"], "completed")
        self.assertEqual(statuses["code_quality_analyst"], "completed")
        self.assertEqual(statuses["security_analyst"], "completed")
        self.assertEqual(statuses["migration_planner"], "completed")
        self.assertGreater(order.index("migration_planner"), max(order.index("code_quality_analyst"), order.index("security_analyst")))
        self.assertIn(("migration_planner", "pool-b"), provider.calls)
        self.assertNotIn(("migration_planner", "pool-a"), provider.calls)
        self.assertEqual(result.agents[4].output["llm_status"], "completed")
        self.assertIn('"upstream_findings"', provider.planner_messages[-1].content)

    def test_unselected_planner_is_skipped_without_gateway_invocation(self):
        result, provider, _ = self.run_case({"code_quality_analyst"})
        planner = next(item for item in result.agents if item.agent_id == "migration_planner")
        self.assertEqual(planner.status, "skipped")
        self.assertNotIn("migration_planner", {agent_id for agent_id, _ in provider.calls})

    def test_selected_upstream_failure_blocks_planner(self):
        result, provider, _ = self.run_case({"migration_planner", "code_quality_analyst"}, no_quality_permission=True)
        statuses = {item.agent_id: item for item in result.agents}
        self.assertEqual(statuses["code_quality_analyst"].status, "failed")
        self.assertEqual(statuses["migration_planner"].status, "skipped")
        self.assertIn("dependency_incomplete", statuses["migration_planner"].error_codes)
        self.assertNotIn("migration_planner", {agent_id for agent_id, _ in provider.calls})
