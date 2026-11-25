import hashlib
import json
import uuid

from django.test import SimpleTestCase
from pydantic import SecretStr

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from agent_foundation.context import ExecutionContext
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import (business_logic_analyst_binding, code_quality_analyst_binding,
    migration_planner_binding, patch_author_binding, register_available_agents,
    security_analyst_binding, structure_analyzer_binding)
from llm_gateway.contracts import ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.credentials import ResolvedCredential
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import (AGENT_POOL_MAP, CredentialReference, PoolConfiguration,
    PoolConfigurations, PoolId, PoolLimits, PoolRoute, QuotaIdentity, QuotaLimits)
from orchestration.contracts import WorkflowRequest
from orchestration.graph import build_workflow, run_workflow


class Reader:
    def __init__(self):
        source = ("def handler(request, cursor):\n"
            "    value = request.GET['name']\n"
            "    cursor.execute(f\"SELECT * FROM users WHERE name='{value}'\")\n"
            "    return value\n")
        raw = source.encode()
        self.files = {"app.py": raw}
        self.records = {path: {"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                        for path, data in self.files.items()}
    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class FakeProvider:
    def __init__(self, fail_agent=None): self.calls, self.fail_agent = [], fail_agent
    def complete(self, route, request, *, timeout_seconds=60.0, credential=None):
        self.calls.append((request.agent_id, credential.credential_id))
        if request.agent_id == self.fail_agent:
            raise RuntimeError("synthetic provider failure")
        if request.agent_id == "business_logic_analyst":
            value = {"executive_overview": "A request value reaches a database query.",
                     "domain_summary": "The visible handler accepts a name and queries records."}
        elif request.agent_id == "migration_planner":
            value = {"overview": "A Python request handler contains a query path.",
                     "database": "Database query code is present.", "database_status": "evidence_found",
                     "data_migration_required": "unknown", "database_evidence_ids": [],
                     "api": "No separate API contract was found.", "api_status": "not_detected"}
        elif request.agent_id == "patch_author":
            prompt = json.loads(request.messages[-1].content.split(":\n", 1)[1])
            finding = next((row for row in prompt["findings"] if row["agent_id"] == "security_analyst"), None)
            if finding is None:
                value = {"summary": "No sufficiently grounded security patch was available."}
            else:
                diff = ("--- a/app.py\n+++ b/app.py\n@@ -1,4 +1,4 @@\n"
                    " def handler(request, cursor):\n"
                    "     value = request.GET['name']\n"
                    "-    cursor.execute(f\"SELECT * FROM users WHERE name='{value}'\")\n"
                    "+    cursor.execute(\"SELECT * FROM users WHERE name=%s\", (value,))\n"
                    "     return value\n")
                ref = next(item for item in finding["evidence"] if item["path"] == "app.py")
                value = {"summary": "Use a parameterized query while preserving the handler return behavior.",
                    "changes": [{"path": "app.py", "unified_diff": diff,
                        "finding_ids": [finding["finding_id"]], "evidence_ids": [ref["evidence_id"]],
                        "rationale": "Untrusted request data is interpolated into SQL.",
                        "expected_behavior": "The same record lookup occurs with parameter binding.", "risk": "medium",
                        "compatibility_assessment": "The SQL API remains the same; validate the database driver parameter style.",
                        "required_tests": ["Add a regression test for parameter binding."],
                        "uncertainty": "Database driver semantics should be verified by the owning team."}]}
        else:
            value = {"overview": "Bounded synthetic specialist interpretation."}
        return ProviderResponse(content=value, usage=Usage(input_tokens=25, output_tokens=18))


class Resolver:
    def resolve(self, reference): return ResolvedCredential(reference.credential_id, SecretStr("fake-only"))


def _pool(pool_id, credential_id):
    if pool_id == PoolId.POOL_C: return PoolConfiguration(pool_id=pool_id)
    route = ModelRoute(provider_id="synthetic", model_id="free-test", tier=ModelTier.FREE,
        supported_tasks={"analysis"}, supports_structured_output=True)
    ref = CredentialReference(credential_id=credential_id,
        secret_ref=f"secret-ref:test/{credential_id}",
        identity=QuotaIdentity(provider_id="synthetic", account_id=credential_id,
            project_id=credential_id, quota_bucket_id=credential_id))
    return PoolConfiguration(pool_id=pool_id, enabled=True,
        routes=(PoolRoute(route=route, credentials=(ref,)),),
        limits=PoolLimits(max_requests_per_minute=60, max_requests_per_execution=12,
            max_requests_per_agent_per_hour=100, max_input_bytes_per_request=200_000,
            max_output_tokens_per_request=8000, max_input_tokens_per_hour=500_000,
            max_output_tokens_per_hour=500_000, max_concurrent_requests=4),
        quota_limits=QuotaLimits(max_requests_per_minute=60, max_input_tokens_per_hour=500_000,
            max_output_tokens_per_hour=500_000, max_concurrent_requests=4))


class PatchAuthorWorkflowTests(SimpleTestCase):
    def run_case(self, *, agents, groups=None, fail_agent=None, omit_patch_propose=False, deny_llm_agent=None):
        reader, registry = Reader(), AgentRegistry()
        register_available_agents(registry, reader)
        provider = FakeProvider(fail_agent)
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
            providers={"synthetic": provider}, pool_configurations=PoolConfigurations(pools={
                PoolId.POOL_A: _pool(PoolId.POOL_A, "pool-a"),
                PoolId.POOL_B: _pool(PoolId.POOL_B, "pool-b"),
                PoolId.POOL_C: _pool(PoolId.POOL_C, "pool-c")} ), credential_resolver=Resolver())
        order = []
        def context_factory(req, agent_id, execution_id):
            permissions = {Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ}
            if agent_id != "structure_analyzer" and agent_id != deny_llm_agent: permissions.add(Permission.LLM_INVOKE)
            if agent_id == "patch_author" and not omit_patch_propose: permissions.add(Permission.PATCH_PROPOSE)
            return ExecutionContext.make(principal_id="synthetic", workspace_id=req.workspace_id,
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
            "migration_planner": migration_planner_binding(context_factory),
            "patch_author": patch_author_binding(context_factory)}
        workflow = build_workflow(registry, ExecutionLifecycle(registry, chat_model_factory=model_factory),
            bindings, observer=lambda event: order.append(event["agent_id"]))
        request = WorkflowRequest(execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(), commit_sha="a" * 40, manifest_digest="b" * 64,
            correlation_id="patch-graph-test", trace_id="patch-graph-test", agents=frozenset(agents),
            groups=frozenset(groups or {"code_quality_security_planning", "patch_engineering"}))
        return run_workflow(workflow, request), provider, order, reader

    def test_actual_graph_generates_pool_b_proposal_after_upstream_agents(self):
        selected = {"code_quality_analyst", "security_analyst", "migration_planner", "patch_author"}
        result, provider, order, reader = self.run_case(agents=selected)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(AGENT_POOL_MAP["patch_author"], PoolId.POOL_B)
        self.assertEqual(agents["patch_author"].status, "completed", agents["patch_author"].error_codes)
        self.assertEqual(agents["patch_author"].output["status"], "awaiting_review")
        self.assertTrue(agents["patch_author"].output["proposals"])
        self.assertGreater(order.index("patch_author"), order.index("security_analyst"))
        self.assertIn(("patch_author", "pool-b"), provider.calls)
        self.assertNotIn(("patch_author", "pool-a"), provider.calls)
        self.assertEqual(reader.files["app.py"], ("def handler(request, cursor):\n"
            "    value = request.GET['name']\n"
            "    cursor.execute(f\"SELECT * FROM users WHERE name='{value}'\")\n"
            "    return value\n").encode())
        self.assertFalse(agents["patch_author"].output["approved"] or agents["patch_author"].output["applied"])

    def test_patch_agent_unselected_is_skipped_and_never_invoked(self):
        result, provider, _, _ = self.run_case(agents={"code_quality_analyst"}, groups={"patch_engineering"})
        agent = next(row for row in result.agents if row.agent_id == "patch_author")
        self.assertEqual(agent.status, "skipped")
        self.assertNotIn("patch_author", {name for name, _ in provider.calls})

    def test_selected_patch_can_run_with_optional_upstream_agents_skipped(self):
        result, provider, _, _ = self.run_case(agents={"patch_author"}, groups={"patch_engineering"})
        agent = next(row for row in result.agents if row.agent_id == "patch_author")
        self.assertEqual(agent.status, "completed")
        self.assertEqual(agent.output["status"], "abstained")
        self.assertTrue({"code_quality_analyst", "security_analyst", "migration_planner"} <=
                        set(agent.output["missing_upstream_agents"]))
        if ("patch_author", "pool-b") in provider.calls:
            self.assertNotIn(("patch_author", "pool-a"), provider.calls)

    def test_missing_proposal_permission_fails_closed_without_provider_call(self):
        selected = {"code_quality_analyst", "security_analyst", "migration_planner", "patch_author"}
        result, provider, _, _ = self.run_case(agents=selected, omit_patch_propose=True)
        agent = next(row for row in result.agents if row.agent_id == "patch_author")
        self.assertEqual(agent.status, "failed")
        self.assertNotIn("patch_author", {name for name, _ in provider.calls})

    def test_mandatory_agent2_failure_blocks_selected_patch_author(self):
        result, provider, _, _ = self.run_case(agents={"patch_author"}, groups={"patch_engineering"},
            deny_llm_agent="business_logic_analyst")
        agents = {row.agent_id: row for row in result.agents}
        self.assertEqual(agents["business_logic_analyst"].status, "failed")
        self.assertEqual(agents["patch_author"].status, "skipped")
        self.assertIn("dependency_incomplete", agents["patch_author"].error_codes)
        self.assertNotIn("patch_author", {name for name, _ in provider.calls})
