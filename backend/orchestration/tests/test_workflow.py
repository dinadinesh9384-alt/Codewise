import hashlib
import uuid

from django.test import SimpleTestCase
from pydantic import SecretStr

from agent_foundation.context import ExecutionContext
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry
from agents.registry import (
    register_available_agents, structure_analyzer_binding,
    business_logic_analyst_binding, code_quality_analyst_binding,
    patch_author_binding, test_engineer_binding, review_agent_binding,
)
from llm_gateway.contracts import ModelRoute, ModelTier, ProviderResponse, Usage
from llm_gateway.credentials import ResolvedCredential
from llm_gateway.gateway import LLMGateway
from llm_gateway.policy import GatewayPolicy
from llm_gateway.pool_contracts import (CredentialReference, PoolConfiguration, PoolId, PoolLimits,
    PoolRoute, PoolConfigurations, QuotaIdentity, QuotaLimits, AGENT_POOL_MAP)
from orchestration.contracts import GROUPS, WorkflowRequest
from orchestration.graph import (build_workflow, in_memory_checkpointer_for_tests,
                                 resume_workflow, run_workflow)


class EmptySnapshotReader:
    def list_file_metadata(self, context): return ()
    def read_file(self, context, path, *, max_bytes=1_000_000): return b""


class WorkflowTests(SimpleTestCase):
    def setUp(self):
        self.request = WorkflowRequest(execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(), commit_sha="a" * 40,
            manifest_digest="b" * 64, correlation_id="corr", trace_id="trace")

    def context_factory(self, request, agent_id, execution_id):
        return ExecutionContext.make(principal_id="test", workspace_id=request.workspace_id,
            repository_id=request.repository_id, snapshot_id=request.snapshot_id, commit_sha=request.commit_sha,
            manifest_digest=request.manifest_digest, execution_id=execution_id, agent_id=agent_id,
            permissions=frozenset({Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ}),
            correlation_id=request.correlation_id, trace_id=request.trace_id)

    def _approval_state(self, *, review_status="ready_for_human_review", llm_status="completed",
                        review_lifecycle="completed", include_review=True, valid_patch=True):
        from importlib import import_module
        from agent_foundation.contracts.evidence import EvidenceReference
        from agent_foundation.contracts.findings import FindingCategory, Severity

        patch_schema = import_module("agents.06_patch_author.schemas.output")
        patch_input = import_module("agents.06_patch_author.schemas.input")
        review_schema = import_module("agents.08_review_agent.schemas.output")
        review_input = import_module("agents.08_review_agent.schemas.input")
        execution_id = self.request.execution_id
        evidence = EvidenceReference(evidence_id="evidence-approval", evidence_type="file",
            snapshot_id=self.request.snapshot_id, manifest_digest=self.request.manifest_digest,
            path="app.py", content_hash="c" * 64, source_tool_id="test")
        source_finding = patch_input.PatchFinding(agent_id="business_logic_analyst",
            finding_id="finding-approval", category=FindingCategory.BUSINESS_LOGIC,
            title="Bounded finding", description="A synthetic finding supporting the patch.",
            severity=Severity.MEDIUM, confidence=0.9, evidence=(evidence,), related_files=("app.py",))
        proposal = patch_schema.PatchProposal(proposal_id="patch_0123456789abcdef", path="app.py",
            original_content_hash="c" * 64, proposed_content_hash="d" * 64,
            unified_diff="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            related_finding_ids=(source_finding.finding_id,), evidence_ids=(evidence.evidence_id,),
            rationale="Address the finding.", expected_behavior="The intended behavior is preserved.", risk="low",
            compatibility_assessment="No public interface changes.", required_tests=("test_behavior",),
            uncertainty="Runtime tests have not been executed.")
        patch_output = patch_schema.PatchAuthorOutput(agent_version="1.0.0",
            execution_id=uuid.uuid5(execution_id, "patch_author"), repository_id=self.request.repository_id,
            snapshot_id=self.request.snapshot_id, commit_sha=self.request.commit_sha,
            manifest_digest=self.request.manifest_digest, status="awaiting_review", llm_status="completed",
            validation_status="passed", summary="Validated proposal for human review.", proposals=(proposal,),
            source_findings=(source_finding,), evidence=(evidence,)).model_dump(mode="json")
        if not valid_patch:
            patch_output["validation_status"] = "not_run"

        review_output = None
        if include_review:
            reviewed_agents = []
            for agent_id in ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                             "security_analyst", "migration_planner", "patch_author", "test_engineer"):
                reviewed_agents.append(review_input.UpstreamReviewStatus(agent_id=agent_id, status="completed",
                    selected=True, output_present=True,
                    llm_status="not_attempted" if agent_id == "structure_analyzer" else "completed",
                    analysis_status="complete", validation_status="passed"))
            review = review_schema.ReviewAgentOutput(agent_version="1.0.0",
                execution_id=uuid.uuid5(execution_id, "review_agent"), repository_id=self.request.repository_id,
                snapshot_id=self.request.snapshot_id, commit_sha=self.request.commit_sha,
                manifest_digest=self.request.manifest_digest, review_status=review_status,
                llm_status=llm_status, overall_readiness=review_status,
                executive_summary="Synthetic review result.", reviewed_agents=tuple(reviewed_agents),
                patch_reviews=(review_schema.PatchReview(proposal_id=proposal.proposal_id,
                    validation_status="verified_in_memory", path=proposal.path,
                    detail="Diff and source hash verified."),))
            review_output = review.model_dump(mode="json")
        statuses = {agent_id: "completed" for agent_id in
                    ("structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                     "security_analyst", "migration_planner", "patch_author", "test_engineer")}
        results = {agent_id: {} for agent_id in statuses}
        results["patch_author"] = patch_output
        if include_review:
            statuses["review_agent"] = review_lifecycle
        else:
            statuses["review_agent"] = "skipped"
        return {"execution_id": str(execution_id), "workspace_id": str(self.request.workspace_id),
            "repository_id": str(self.request.repository_id), "snapshot_id": str(self.request.snapshot_id),
            "commit_sha": self.request.commit_sha, "manifest_digest": self.request.manifest_digest,
            "approval_required": True,
            "selected": {**{agent_id: True for agent_id in statuses}, "review_agent": include_review},
            "statuses": statuses,
            "agent_results": {**results, **({"review_agent": review_output} if include_review else {})},
            "errors": {}}

    @staticmethod
    def _invoke_approval_gate(state):
        from langgraph.graph import END, START, StateGraph
        from orchestration.graph import _approval_node
        from orchestration.state import WorkflowState
        graph = StateGraph(WorkflowState)
        graph.add_node("approval_gate", _approval_node)
        graph.add_edge(START, "approval_gate")
        graph.add_edge("approval_gate", END)
        return graph.compile().invoke(state)

    def test_real_agent_runs_via_lifecycle_and_missing_agents_are_reported(self):
        registry = AgentRegistry()
        register_available_agents(registry, EmptySnapshotReader())
        binding = structure_analyzer_binding(self.context_factory)
        workflow = build_workflow(registry, ExecutionLifecycle(registry), {"structure_analyzer": binding})
        result = run_workflow(workflow, self.request)
        agents = {agent.agent_id: agent for agent in result.agents}
        self.assertEqual(agents["structure_analyzer"].status, "completed")
        self.assertEqual(agents["business_logic_analyst"].status, "unavailable")
        self.assertEqual(result.status, "partial")
        self.assertEqual(agents["structure_analyzer"].output["agent_id"], "structure_analyzer")

    def test_optional_groups_can_be_disabled_but_mandatory_agents_remain_selected(self):
        self.assertTrue(all(self.request.selected(agent) for agent in
                            ("code_quality_analyst", "security_analyst", "migration_planner",
                             "patch_author", "test_engineer", "review_agent")))
        request = self.request.model_copy(update={"groups": frozenset(), "agents": frozenset({"structure_analyzer"})})
        self.assertTrue(request.selected("structure_analyzer"))
        self.assertTrue(request.selected("business_logic_analyst"))
        self.assertFalse(request.selected("patch_author"))

    def test_approval_is_not_requested_when_no_patch_agent_is_available(self):
        registry = AgentRegistry()
        register_available_agents(registry, EmptySnapshotReader())
        workflow = build_workflow(registry, ExecutionLifecycle(registry),
            {"structure_analyzer": structure_analyzer_binding(self.context_factory)})
        request = self.request.model_copy(update={"approval_required": True})
        result = run_workflow(workflow, request)
        self.assertEqual(result.approval, "not_applicable")

    def test_checkpointer_is_an_actual_langgraph_saver(self):
        from langgraph.checkpoint.memory import InMemorySaver
        self.assertIsInstance(in_memory_checkpointer_for_tests(), InMemorySaver)

    def test_human_approval_pauses_and_resumes_with_memory_checkpointer(self):
        from langgraph.graph import END, START, StateGraph
        from orchestration.graph import WorkflowGraph, _approval_node
        from orchestration.state import WorkflowState
        graph = StateGraph(WorkflowState)
        graph.add_node("approval_gate", _approval_node)
        graph.add_edge(START, "approval_gate")
        graph.add_edge("approval_gate", END)
        workflow = WorkflowGraph(graph.compile(checkpointer=in_memory_checkpointer_for_tests()),
                                 has_checkpointer=True, can_produce_patch=True, can_approve=True)
        request = self.request.model_copy(update={"approval_required": True})
        config = {"configurable": {"thread_id": str(request.execution_id)}}
        state = self._approval_state()
        pending = workflow.invoke(state, config=config)
        self.assertTrue(pending.get("__interrupt__"))
        approved = workflow.resume("approved", config=config)
        self.assertEqual(approved["approval"], "approved")

    def test_approval_start_and_resume_reject_a_foreign_execution_thread(self):
        from langgraph.graph import END, START, StateGraph
        from orchestration.graph import WorkflowGraph
        from orchestration.state import WorkflowState

        graph = StateGraph(WorkflowState)
        graph.add_node("approval_gate", lambda state: {"approval": "not_applicable"})
        graph.add_edge(START, "approval_gate")
        graph.add_edge("approval_gate", END)
        workflow = WorkflowGraph(graph.compile(checkpointer=in_memory_checkpointer_for_tests()),
            has_checkpointer=True, can_produce_patch=True, can_approve=True)
        request = self.request.model_copy(update={"approval_required": True})
        foreign_config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        with self.assertRaisesRegex(ValueError, "identity must match"):
            run_workflow(workflow, request, config=foreign_config)
        with self.assertRaisesRegex(ValueError, "identity must match"):
            resume_workflow(workflow, request, "approved", config=foreign_config)

    def test_no_approval_interrupt_without_a_validated_patch_proposal(self):
        state = self._approval_state()
        state["agent_results"]["patch_author"]["status"] = "abstained"
        state["agent_results"]["patch_author"]["proposals"] = []
        result = self._invoke_approval_gate(state)
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(result["approval"], "not_applicable")

    def test_ready_review_outcomes_reach_human_approval_with_readiness_disclosed(self):
        for readiness in ("ready_for_human_review", "ready_with_warnings"):
            with self.subTest(readiness=readiness):
                result = self._invoke_approval_gate(self._approval_state(review_status=readiness))
                self.assertTrue(result.get("__interrupt__"))
                payload = result["__interrupt__"][0].value
                self.assertEqual(payload["review_status"], readiness)
                self.assertIn("human review is still required", payload["message"])

    def test_incomplete_blocked_changes_required_and_unavailable_reviews_block_approval(self):
        for readiness, llm_status in (("incomplete", "unavailable"), ("blocked", "completed"),
                                      ("changes_required", "completed")):
            with self.subTest(readiness=readiness):
                result = self._invoke_approval_gate(self._approval_state(
                    review_status=readiness, llm_status=llm_status))
                self.assertFalse(result.get("__interrupt__"))
                self.assertEqual(result["approval"], "not_applicable")
        # Exact Task 8A reproduction: lifecycle completed, unavailable model, incomplete output.
        result = self._invoke_approval_gate(self._approval_state(review_status="incomplete",
            llm_status="unavailable", review_lifecycle="completed"))
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(result["approval"], "not_applicable")

    def test_missing_invalid_failed_and_unavailable_review_block_approval(self):
        missing = self._approval_state(include_review=False)
        result = self._invoke_approval_gate(missing)
        self.assertFalse(result.get("__interrupt__"))
        self.assertIn("ai_final_review_not_performed", str(result.get("events", ())))

        invalid = self._approval_state()
        invalid["agent_results"]["review_agent"] = {"review_status": "ready_for_human_review"}
        result = self._invoke_approval_gate(invalid)
        self.assertFalse(result.get("__interrupt__"))

        for lifecycle in ("failed", "unavailable", "skipped"):
            with self.subTest(lifecycle=lifecycle):
                result = self._invoke_approval_gate(self._approval_state(review_lifecycle=lifecycle))
                self.assertFalse(result.get("__interrupt__"))

    def test_invalid_patch_output_or_proposal_blocks_approval(self):
        for corrupt in ("validation_status", "proposal_id", "snapshot_identity"):
            with self.subTest(corrupt=corrupt):
                state = self._approval_state()
                if corrupt == "validation_status":
                    state["agent_results"]["patch_author"]["validation_status"] = "not_run"
                elif corrupt == "proposal_id":
                    state["agent_results"]["patch_author"]["proposals"][0]["proposal_id"] = "fabricated"
                else:
                    state["agent_results"]["patch_author"]["snapshot_id"] = str(uuid.uuid4())
                result = self._invoke_approval_gate(state)
                self.assertFalse(result.get("__interrupt__"))

    def test_review_requires_proposal_validation_and_current_snapshot_identity(self):
        for corrupt in ("missing_patch_review", "snapshot_identity"):
            with self.subTest(corrupt=corrupt):
                state = self._approval_state()
                if corrupt == "missing_patch_review":
                    state["agent_results"]["review_agent"]["patch_reviews"] = []
                else:
                    state["agent_results"]["review_agent"]["snapshot_id"] = str(uuid.uuid4())
                result = self._invoke_approval_gate(state)
                self.assertFalse(result.get("__interrupt__"))

    def test_review_execution_identity_must_match_the_current_workflow(self):
        state = self._approval_state()
        state["agent_results"]["review_agent"]["execution_id"] = str(uuid.uuid4())
        result = self._invoke_approval_gate(state)
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(result["approval"], "not_applicable")

    def test_reviewed_upstream_lifecycle_provenance_must_match_workflow_state(self):
        state = self._approval_state()
        state["agent_results"]["review_agent"]["reviewed_agents"][5]["status"] = "unavailable"
        result = self._invoke_approval_gate(state)
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(result["approval"], "not_applicable")

    def test_final_review_status_preserves_readiness_lifecycle_and_selection(self):
        from importlib import import_module
        from orchestration.graph import _as_workflow_result
        request = self.request.model_copy(update={"groups": frozenset({"final_review"})})
        for readiness, expected in (("ready_for_human_review", "ready_for_human_review"),
                                    ("ready_with_warnings", "ready_with_warnings"),
                                    ("incomplete", "incomplete"), ("blocked", "blocked"),
                                    ("changes_required", "changes_required")):
            state = self._approval_state(review_status=readiness)
            state.update({"workflow_status": "partial"})
            result = _as_workflow_result(request, state)
            self.assertEqual(result.final_review_status, expected)
        missing_output = {"workflow_status": "partial", "statuses": {"review_agent": "completed"},
                          "agent_results": {}, "errors": {}}
        self.assertEqual(_as_workflow_result(request, missing_output).final_review_status, "incomplete")
        invalid_output = {"workflow_status": "partial", "statuses": {"review_agent": "completed"},
                          "agent_results": {"review_agent": {"review_status": "ready_for_human_review"}},
                          "errors": {}}
        self.assertEqual(_as_workflow_result(request, invalid_output).final_review_status, "failed")
        failed = {"workflow_status": "partial", "statuses": {"review_agent": "failed"}, "errors": {}}
        self.assertEqual(_as_workflow_result(request, failed).final_review_status, "failed")
        unavailable = {"workflow_status": "partial", "statuses": {"review_agent": "unavailable"}, "errors": {}}
        self.assertEqual(_as_workflow_result(request, unavailable).final_review_status, "unavailable")
        unselected = request.model_copy(update={"groups": frozenset()})
        self.assertEqual(_as_workflow_result(unselected, {}).final_review_status, "not_performed")


# ---------------------------------------------------------------------------
# F3 — Agent 3 LangGraph integration: run path and skip path
# ---------------------------------------------------------------------------

class _MinimalPythonReader:
    """Snapshot reader that serves a single minimal Python file."""
    def __init__(self, files):
        self.files = {path: content.encode("utf-8") for path, content in files.items()}
        self.records = {path: {"path": path, "size_bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()}
                        for path, data in self.files.items()}

    def list_file_metadata(self, context): return tuple(self.records.values())
    def list_paths(self, context): return tuple(self.files)
    def file_metadata(self, context, path): return self.records[path]
    def read_file(self, context, path, *, max_bytes=1_000_000): return self.files[path]


class _FakeStructuredProvider:
    """Provides a valid structured LLM response with no network access."""
    def __init__(self, response_dict):
        self._response = response_dict
        self.calls = []
        self.pool_calls = []
        self.requests = []

    def complete(self, route, request, *, timeout_seconds=60.0, credential=None):
        self.calls.append(request.agent_id)
        self.pool_calls.append((request.agent_id, request.pool_id))
        self.requests.append(request)
        response = self._response.get(request.agent_id, self._response) if isinstance(self._response, dict) else self._response
        return ProviderResponse(content=response,
                                usage=Usage(input_tokens=15, output_tokens=8))


class Agent3LangGraphIntegrationTests(SimpleTestCase):
    """F3: Verify Agent 3 executes correctly through the shared LangGraph workflow.

    Uses a fake Pool A credential and provider; no network or paid calls occur.
    Agent 1 (structure_analyzer) and Agent 2 (business_logic_analyst) are
    exercised as mandatory nodes; Agent 3 is exercised as an optional node
    controlled by the code_quality_security_planning group.
    """

    _MINIMAL_SOURCE = "def compute(a: int, b: int) -> int:\n    return a + b\n"

    def setUp(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        self.execution_order = []
        self.commit = "a" * 40
        self.digest = "b" * 64
        self.base_request = WorkflowRequest(
            execution_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(),
            commit_sha=self.commit, manifest_digest=self.digest,
            correlation_id="f3-test", trace_id="f3-trace")

    def _make_llm_gateway(self):
        """Build a fake LLMGateway that serves both Agent 2 and Agent 3."""
        route = ModelRoute(provider_id="fake", model_id="fake-model", tier=ModelTier.FREE,
                           supported_tasks=frozenset({"analysis"}), supports_structured_output=True)
        provider = _FakeStructuredProvider({"overview": "Synthetic LLM overview for integration test."})
        pool = PoolConfiguration(pool_id=PoolId.POOL_A, enabled=True,
            routes=(PoolRoute(route=route, credentials=(CredentialReference(
                credential_id="f3-test", secret_ref="CODEWISE_LLM_CREDENTIAL_F3_TEST",
                identity=QuotaIdentity(provider_id="fake", account_id="f3-test", project_id="f3-test",
                                       quota_bucket_id="f3-test")),)),),
            limits=PoolLimits(max_requests_per_minute=50, max_requests_per_execution=8,
                max_requests_per_agent_per_hour=80, max_input_bytes_per_request=200_000,
                max_output_tokens_per_request=32768, max_input_tokens_per_hour=1_000_000,
                max_output_tokens_per_hour=1_000_000, max_concurrent_requests=4),
            quota_limits=QuotaLimits(max_requests_per_minute=50, max_input_tokens_per_hour=1_000_000,
                max_output_tokens_per_hour=1_000_000, max_concurrent_requests=4))
        class Resolver:
            def resolve(self, reference):
                return ResolvedCredential(reference.credential_id, SecretStr("synthetic-test-only"))
        pools = PoolConfigurations(pools={PoolId.POOL_A: pool,
            PoolId.POOL_B: PoolConfiguration(pool_id=PoolId.POOL_B),
            PoolId.POOL_C: PoolConfiguration(pool_id=PoolId.POOL_C)})
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
            routes=(route,), providers={"fake": provider}, pool_configurations=pools,
            credential_resolver=Resolver())
        gateway.test_provider = provider
        return gateway

    def _context_factory(self, gateway, llm_permissions, *, deny_agent2_llm=False):
        """Return a context_factory closure that embeds the gateway."""
        from agent_foundation.adapters.langchain_adapter import GatewayChatModel

        def factory(request, agent_id, execution_id):
            permissions = frozenset({Permission.ANALYSIS_RUN, Permission.REPOSITORY_READ})
            if agent_id != "structure_analyzer":
                permissions |= llm_permissions
            if agent_id == "patch_author":
                permissions |= {Permission.PATCH_PROPOSE}
            if deny_agent2_llm and agent_id == "business_logic_analyst":
                permissions -= {Permission.LLM_INVOKE}
            ctx = ExecutionContext.make(
                principal_id="f3-test", workspace_id=request.workspace_id,
                repository_id=request.repository_id, snapshot_id=request.snapshot_id,
                commit_sha=request.commit_sha, manifest_digest=request.manifest_digest,
                execution_id=execution_id, agent_id=agent_id,
                permissions=permissions,
                correlation_id=request.correlation_id, trace_id=request.trace_id)
            return ctx

        def model_factory(definition, exec_request, context):
            return GatewayChatModel(
                gateway=gateway,
                permissions=context.permissions,
                agent_id=definition.contract.agent_id,
                execution_id=str(exec_request.execution_id),
                correlation_id=exec_request.correlation_id,
                trace_id=exec_request.trace_id)

        return factory, model_factory

    def _build_full_workflow(self, gateway, *, deny_agent2_llm=False):
        """Register Agents 1, 2, 3 and compile the shared LangGraph."""
        registry = AgentRegistry()
        register_available_agents(registry, self.reader)

        context_factory, model_factory = self._context_factory(
            gateway, llm_permissions=frozenset({Permission.LLM_INVOKE}),
            deny_agent2_llm=deny_agent2_llm)

        lifecycle = ExecutionLifecycle(registry, chat_model_factory=model_factory)
        bindings = {
            "structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "code_quality_analyst": code_quality_analyst_binding(context_factory),
        }
        return build_workflow(registry, lifecycle, bindings,
            observer=lambda event: self.execution_order.append(event["agent_id"]))

    def _build_patch_test_workflow(self, gateway, *, deny_patch_permission=False, grant_test_execution=False):
        registry = AgentRegistry()
        register_available_agents(registry, self.reader)
        context_factory, model_factory = self._context_factory(
            gateway, llm_permissions=frozenset({Permission.LLM_INVOKE}))
        if deny_patch_permission or grant_test_execution:
            base_context_factory = context_factory
            def context_factory(request, agent_id, execution_id):
                context = base_context_factory(request, agent_id, execution_id)
                if deny_patch_permission and agent_id == "patch_author":
                    context = context.model_copy(update={"permissions": context.permissions - {Permission.PATCH_PROPOSE}})
                if grant_test_execution and agent_id == "test_engineer":
                    context = context.model_copy(update={"permissions": context.permissions | {Permission.TESTS_EXECUTE}})
                return context
        lifecycle = ExecutionLifecycle(registry, chat_model_factory=model_factory)
        bindings = {"structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "patch_author": patch_author_binding(context_factory),
            "test_engineer": test_engineer_binding(context_factory)}
        return build_workflow(registry, lifecycle, bindings,
            observer=lambda event: self.execution_order.append(event["agent_id"]))

    def _build_review_workflow(self, gateway, *, forbid_review_tests=False, fail_quality=False, fail_business=False):
        registry = AgentRegistry()
        register_available_agents(registry, self.reader)
        context_factory, model_factory = self._context_factory(
            gateway, llm_permissions=frozenset({Permission.LLM_INVOKE}))
        if forbid_review_tests or fail_quality or fail_business:
            base = context_factory
            def context_factory(request, agent_id, execution_id):
                context = base(request, agent_id, execution_id)
                if forbid_review_tests and agent_id == "review_agent":
                    context = context.model_copy(update={"permissions": context.permissions | {Permission.TESTS_EXECUTE}})
                if fail_quality and agent_id == "code_quality_analyst":
                    context = context.model_copy(update={"permissions": context.permissions - {Permission.LLM_INVOKE}})
                if fail_business and agent_id == "business_logic_analyst":
                    context = context.model_copy(update={"permissions": context.permissions - {Permission.LLM_INVOKE}})
                return context
        lifecycle = ExecutionLifecycle(registry, chat_model_factory=model_factory)
        bindings = {"structure_analyzer": structure_analyzer_binding(context_factory),
            "business_logic_analyst": business_logic_analyst_binding(context_factory),
            "review_agent": review_agent_binding(context_factory)}
        if fail_quality:
            bindings["code_quality_analyst"] = code_quality_analyst_binding(context_factory)
        return build_workflow(registry, lifecycle, bindings,
            observer=lambda event: self.execution_order.append(event["agent_id"]))

    def _make_patch_test_gateway(self):
        route = ModelRoute(provider_id="fake", model_id="fake-model", tier=ModelTier.FREE,
            supported_tasks=frozenset({"analysis"}), supports_structured_output=True)
        provider = _FakeStructuredProvider({
            "business_logic_analyst": {"overview": "Synthetic business interpretation."},
            "patch_author": {"summary": "No patch is justified by the available evidence.", "changes": [], "uncertainties": []},
            "test_engineer": {"summary": "Test inventory reviewed; no specific scenario can be supported.",
                "scenarios": [], "uncertainties": []},
            "review_agent": {"executive_summary": "Mandatory analysis was reviewed; optional analyses remain unavailable.",
                "summary_upstream_ids": ["business_logic_analyst"],
                "assessments": [], "conflicts": [], "uncertainties": []}})
        pools = {}
        for pool_id in (PoolId.POOL_A, PoolId.POOL_B, PoolId.POOL_C):
            ref = CredentialReference(credential_id=f"test-{pool_id.value}",
                secret_ref=f"CODEWISE_LLM_CREDENTIAL_TEST_{pool_id.value.upper()}",
                identity=QuotaIdentity(provider_id="fake", account_id=pool_id.value, project_id=pool_id.value,
                                       quota_bucket_id=pool_id.value))
            pools[pool_id] = PoolConfiguration(pool_id=pool_id, enabled=True,
                routes=(PoolRoute(route=route, credentials=(ref,)),),
                limits=PoolLimits(max_requests_per_minute=50, max_requests_per_execution=20,
                    max_requests_per_agent_per_hour=80, max_input_bytes_per_request=200_000,
                    max_output_tokens_per_request=32768, max_input_tokens_per_hour=1_000_000,
                    max_output_tokens_per_hour=1_000_000, max_concurrent_requests=4),
                quota_limits=QuotaLimits(max_requests_per_minute=50, max_input_tokens_per_hour=1_000_000,
                    max_output_tokens_per_hour=1_000_000, max_concurrent_requests=4))
        class Resolver:
            def __init__(self): self.calls = []
            def resolve(self, reference):
                self.calls.append(reference.credential_id)
                return ResolvedCredential(reference.credential_id, SecretStr("fake-only"))
        resolver = Resolver()
        gateway = LLMGateway(policy=GatewayPolicy(enabled=True, enabled_tiers={ModelTier.FREE}),
            routes=(route,), providers={"fake": provider}, pool_configurations=PoolConfigurations(pools=pools),
            credential_resolver=resolver)
        gateway.test_provider = provider
        gateway.test_resolver = resolver
        return gateway

    # -- Pool A routing ---------------------------------------------------

    def test_agent3_is_assigned_to_pool_a(self):
        self.assertEqual(AGENT_POOL_MAP.get("code_quality_analyst"), PoolId.POOL_A)

    # -- Optional group selection ------------------------------------------

    def test_agent3_is_selected_when_quality_group_is_active(self):
        self.assertTrue(self.base_request.selected("code_quality_analyst"),
                        "code_quality_analyst must be selected when all groups are active")

    def test_agent3_is_not_selected_when_quality_group_is_absent(self):
        req = self.base_request.model_copy(update={"groups": frozenset()})
        self.assertFalse(req.selected("code_quality_analyst"))

    # -- Skip path ---------------------------------------------------------

    def test_agent3_is_skipped_when_quality_group_is_absent(self):
        """Agent 3 node must report status='skipped' when not in the active groups."""
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        req = self.base_request.model_copy(update={"groups": frozenset()})
        result = run_workflow(workflow, req)
        agents = {a.agent_id: a for a in result.agents}
        self.assertEqual(agents["code_quality_analyst"].status, "skipped")
        self.assertIn("business_logic_analyst", gateway.test_provider.calls)
        self.assertNotIn("code_quality_analyst", gateway.test_provider.calls)

    # -- Mandatory Agent 1 and Agent 2 always run --------------------------

    def test_agents_1_and_2_complete_regardless_of_group_selection(self):
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        # No quality group — agents 1 and 2 must still complete.
        req = self.base_request.model_copy(update={"groups": frozenset()})
        result = run_workflow(workflow, req)
        agents = {a.agent_id: a for a in result.agents}
        self.assertEqual(agents["structure_analyzer"].status, "completed",
                         "Agent 1 must always complete")
        self.assertEqual(agents["business_logic_analyst"].status, "completed",
                         "Agent 2 must always complete")

    # -- Run path ----------------------------------------------------------

    def test_agent3_completes_through_graph_when_quality_group_is_active(self):
        """Agent 3 must complete and appear in graph results when the group is active."""
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        result = run_workflow(workflow, self.base_request)
        agents = {a.agent_id: a for a in result.agents}

        self.assertEqual(agents["structure_analyzer"].status, "completed",
                         "Agent 1 (mandatory) must complete")
        self.assertEqual(agents["business_logic_analyst"].status, "completed",
                         "Agent 2 (mandatory) must complete")
        self.assertEqual(agents["code_quality_analyst"].status, "completed",
                         f"Agent 3 did not complete. Error codes: {agents['code_quality_analyst'].error_codes}")
        self.assertIn("business_logic_analyst", gateway.test_provider.calls)
        self.assertIn("code_quality_analyst", gateway.test_provider.calls)
        self.assertEqual(self.execution_order[:3], [
            "structure_analyzer", "business_logic_analyst", "code_quality_analyst"])

    def test_agent3_output_contains_expected_fields_in_graph_result(self):
        """Agent 3 graph output must contain standard contract fields."""
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        result = run_workflow(workflow, self.base_request)
        agents = {a.agent_id: a for a in result.agents}
        self.assertEqual(agents["code_quality_analyst"].status, "completed")
        output = agents["code_quality_analyst"].output
        self.assertIsNotNone(output)
        self.assertEqual(output.get("agent_version"), "1.0.0")
        self.assertIn("llm_status", output)
        self.assertIn("analyzed_files", output)

    def test_agent3_snapshot_binding_matches_workflow_request(self):
        """Agent 3 output snapshot_id/commit_sha must match the workflow request."""
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        result = run_workflow(workflow, self.base_request)
        agents = {a.agent_id: a for a in result.agents}
        self.assertEqual(agents["code_quality_analyst"].status, "completed")
        output = agents["code_quality_analyst"].output
        self.assertEqual(output["commit_sha"], self.commit)
        self.assertEqual(output["manifest_digest"], self.digest)

    def test_agents_1_and_2_are_not_broken_by_agent3_additions(self):
        """Confirm Agents 1 and 2 produce valid, unchanged output after Agent 3 was wired."""
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway)
        result = run_workflow(workflow, self.base_request)
        agents = {a.agent_id: a for a in result.agents}

        sa = agents["structure_analyzer"]
        self.assertEqual(sa.status, "completed")
        self.assertEqual(sa.output.get("agent_id"), "structure_analyzer")

        bla = agents["business_logic_analyst"]
        self.assertEqual(bla.status, "completed")
        # Agent 2 always produces an agent_version field.
        self.assertIn("agent_version", bla.output)

    def test_agent3_is_skipped_when_agent2_dependency_fails(self):
        gateway = self._make_llm_gateway()
        workflow = self._build_full_workflow(gateway, deny_agent2_llm=True)
        result = run_workflow(workflow, self.base_request)
        agents = {a.agent_id: a for a in result.agents}
        self.assertEqual(agents["structure_analyzer"].status, "completed")
        self.assertEqual(agents["business_logic_analyst"].status, "failed")
        self.assertEqual(agents["code_quality_analyst"].status, "skipped")
        self.assertIn("dependency_incomplete", agents["code_quality_analyst"].error_codes)
        self.assertNotIn("code_quality_analyst", gateway.test_provider.calls)

    def test_agent7_executes_on_selected_patch_group_after_agent6_and_calls_fake_pool_c(self):
        reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE,
            "tests/test_compute.py": "def test_compute():\n    assert 1 + 1 == 2\n"})
        self.reader = reader
        gateway = self._make_patch_test_gateway()
        workflow = self._build_patch_test_workflow(gateway)
        request = self.base_request.model_copy(update={"groups": frozenset({"patch_engineering"})})
        result = run_workflow(workflow, request)
        agents = {agent.agent_id: agent for agent in result.agents}
        self.assertEqual(agents["structure_analyzer"].status, "completed")
        self.assertEqual(agents["business_logic_analyst"].status, "completed")
        self.assertEqual(agents["patch_author"].status, "completed", agents["patch_author"].error_codes)
        self.assertEqual(agents["test_engineer"].status, "completed")
        self.assertEqual(agents["test_engineer"].output["llm_status"], "completed")
        self.assertIn("test_engineer", gateway.test_provider.calls)
        self.assertIn("test-pool_c", gateway.test_resolver.calls)
        self.assertEqual(AGENT_POOL_MAP["test_engineer"], PoolId.POOL_C)
        self.assertEqual(self.execution_order[-2:], ["patch_author", "test_engineer"])

    def test_agent7_is_skipped_when_patch_group_is_unselected(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_patch_test_workflow(gateway)
        request = self.base_request.model_copy(update={"groups": frozenset()})
        result = run_workflow(workflow, request)
        agents = {agent.agent_id: agent for agent in result.agents}
        self.assertEqual(agents["patch_author"].status, "skipped")
        self.assertEqual(agents["test_engineer"].status, "skipped")
        self.assertNotIn("test_engineer", gateway.test_provider.calls)

    def test_agent7_is_skipped_when_selected_agent6_fails_its_permission_gate(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_patch_test_workflow(gateway, deny_patch_permission=True)
        request = self.base_request.model_copy(update={"groups": frozenset({"patch_engineering"})})
        result = run_workflow(workflow, request)
        agents = {agent.agent_id: agent for agent in result.agents}
        self.assertEqual(agents["patch_author"].status, "failed")
        self.assertEqual(agents["test_engineer"].status, "skipped")
        self.assertIn("dependency_incomplete", agents["test_engineer"].error_codes)
        self.assertNotIn("test_engineer", gateway.test_provider.calls)

    def test_agent7_fails_closed_if_tests_execute_permission_is_granted(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_patch_test_workflow(gateway, grant_test_execution=True)
        request = self.base_request.model_copy(update={"groups": frozenset({"patch_engineering"})})
        result = run_workflow(workflow, request)
        agents = {agent.agent_id: agent for agent in result.agents}
        self.assertEqual(agents["patch_author"].status, "completed")
        self.assertEqual(agents["test_engineer"].status, "failed")
        self.assertIn("forbidden_test_execution_permission", agents["test_engineer"].error_codes)
        self.assertNotIn("test_engineer", gateway.test_provider.calls)

    def test_agent8_runs_after_mandatory_outputs_and_reports_optional_gaps(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_review_workflow(gateway)
        request = self.base_request.model_copy(update={"groups": frozenset({"final_review"})})
        result = run_workflow(workflow, request)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["structure_analyzer"].status, "completed")
        self.assertEqual(agents["business_logic_analyst"].status, "completed")
        self.assertEqual(agents["review_agent"].status, "completed", agents["review_agent"].error_codes)
        self.assertEqual(result.final_review_status, "incomplete")
        self.assertEqual(agents["review_agent"].output["review_status"], "incomplete",
                         agents["review_agent"].output)
        self.assertIn("code_quality_analyst", agents["review_agent"].output["skipped_agents"])
        self.assertIn("review_agent", gateway.test_provider.calls)
        request_sent = next(row for row in gateway.test_provider.requests if row.agent_id == "review_agent")
        self.assertIn("business_logic_analyst", request_sent.messages[1].content)
        self.assertIn('"repository"', request_sent.messages[1].content)
        self.assertIn("test-pool_c", gateway.test_resolver.calls)

    def test_agent8_unselected_is_explicitly_not_performed(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_review_workflow(gateway)
        request = self.base_request.model_copy(update={"groups": frozenset()})
        result = run_workflow(workflow, request)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["review_agent"].status, "skipped")
        self.assertEqual(result.final_review_status, "not_performed")
        self.assertNotIn("review_agent", gateway.test_provider.calls)

    def test_agent8_fails_closed_on_test_execution_permission(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_review_workflow(gateway, forbid_review_tests=True)
        request = self.base_request.model_copy(update={"groups": frozenset({"final_review"})})
        result = run_workflow(workflow, request)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["review_agent"].status, "failed")
        self.assertEqual(result.final_review_status, "failed")
        self.assertIn("forbidden_test_execution_permission", agents["review_agent"].error_codes)
        self.assertNotIn("review_agent", gateway.test_provider.calls)

    def test_agent8_reviews_after_a_selected_optional_agent_fails(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_review_workflow(gateway, fail_quality=True)
        request = self.base_request.model_copy(update={"groups": frozenset({
            "code_quality_security_planning", "final_review"})})
        result = run_workflow(workflow, request)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["code_quality_analyst"].status, "failed")
        self.assertEqual(agents["review_agent"].status, "completed", agents["review_agent"].error_codes)
        self.assertIn("code_quality_analyst", agents["review_agent"].output["failed_agents"])
        self.assertEqual(agents["review_agent"].output["review_status"], "incomplete")
        self.assertIn("review_agent", gateway.test_provider.calls)

    def test_agent8_is_blocked_when_mandatory_agent_two_fails(self):
        self.reader = _MinimalPythonReader({"app/services/compute.py": self._MINIMAL_SOURCE})
        gateway = self._make_patch_test_gateway()
        workflow = self._build_review_workflow(gateway, fail_business=True)
        request = self.base_request.model_copy(update={"groups": frozenset({"final_review"})})
        result = run_workflow(workflow, request)
        agents = {item.agent_id: item for item in result.agents}
        self.assertEqual(agents["business_logic_analyst"].status, "failed")
        self.assertEqual(agents["review_agent"].status, "skipped")
        self.assertEqual(result.final_review_status, "blocked")
        self.assertNotIn("review_agent", gateway.test_provider.calls)
