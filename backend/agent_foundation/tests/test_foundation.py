import threading
import uuid
from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase
from pydantic import BaseModel, ConfigDict, Field

from agent_foundation.adapters.langchain_adapter import GatewayChatModel
from langgraph.graph import END, START, StateGraph
from agent_foundation.context import ExecutionContext, validate_relative_path
from agent_foundation.contracts.agent import AgentCapability
from agent_foundation.contracts.execution import ExecutionRequest, ExecutionStatus
from agent_foundation.contracts.evidence import EvidenceReference, EvidenceType
from agent_foundation.contracts.findings import Finding, FindingCategory, Severity, VerificationStatus
from agent_foundation.errors import DuplicateAgentError, InvalidExecutionTransitionError, PermissionDeniedError
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentDefinition, AgentRegistry
from agent_foundation.tools import ToolDefinition, ToolRegistry, invoke_tool
from agent_foundation.contracts.tools import ToolCategory, ToolContract


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str


class FoundationTests(SimpleTestCase):
    def context(self, **overrides):
        values = dict(principal_id="test-user", workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(), commit_sha="a"*40, manifest_digest="b"*64, execution_id=uuid.uuid4(),
            agent_id="structure_analyzer", permissions=frozenset({Permission.ANALYSIS_RUN}),
            correlation_id="corr", trace_id="trace", cancellation=threading.Event())
        values.update(overrides)
        return ExecutionContext.make(**values)

    def test_deterministic_agent_registers_and_executes_without_llm(self):
        registry = AgentRegistry()
        definition = AgentDefinition.create(agent_id="structure_analyzer", display_name="Structure",
            description="Deterministic test agent", version="1", input_model=Input, output_model=Output,
            runner=lambda value, context: {"summary": value.text.upper()},
            capabilities={AgentCapability.DETERMINISTIC}, required_permissions={Permission.ANALYSIS_RUN})
        registry.register(definition)
        request = ExecutionRequest(execution_id=uuid.uuid4(), agent_id="structure_analyzer", agent_version="1",
            input={"text": "ok"}, repository_id=uuid.uuid4(), snapshot_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            commit_sha="a"*40, manifest_digest="b"*64, requested_capabilities=frozenset(), correlation_id="corr", trace_id="trace")
        ctx = self.context(execution_id=request.execution_id, workspace_id=request.workspace_id,
            repository_id=request.repository_id, snapshot_id=request.snapshot_id)
        result = ExecutionLifecycle(registry).execute(request, ctx)
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(result.output.summary, "OK")
        self.assertIsNone(result.model_usage)

    def test_structure_analyzer_context_cannot_receive_llm_permission(self):
        called = []
        registry = AgentRegistry()
        definition = AgentDefinition.create(agent_id="structure_analyzer", display_name="Structure",
            description="deterministic test", version="1", input_model=Input, output_model=Output,
            runner=lambda value, context: (called.append(True) or {"summary": "unexpected"}),
            capabilities={AgentCapability.DETERMINISTIC}, required_permissions={Permission.ANALYSIS_RUN})
        registry.register(definition)
        ctx = self.context(permissions=frozenset({Permission.ANALYSIS_RUN, Permission.LLM_INVOKE}))
        request = ExecutionRequest(execution_id=ctx.execution_id, agent_id="structure_analyzer", agent_version="1",
            input={"text": "must not run"}, repository_id=ctx.repository_id, snapshot_id=ctx.snapshot_id,
            workspace_id=ctx.workspace_id, commit_sha=ctx.commit_sha, manifest_digest=ctx.manifest_digest,
            correlation_id=ctx.correlation_id, trace_id=ctx.trace_id)
        result = ExecutionLifecycle(registry).execute(request, ctx)
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.errors[0].code, "permission_denied")
        self.assertEqual(called, [])

    def test_registry_rejects_duplicates_and_state_transition(self):
        registry = AgentRegistry()
        definition = AgentDefinition.create(agent_id="structure_analyzer", display_name="S", description="D", version="1",
            input_model=Input, output_model=Output, runner=lambda value, context: {"summary": "ok"})
        registry.register(definition)
        with self.assertRaises(DuplicateAgentError): registry.register(definition)
        with self.assertRaises(InvalidExecutionTransitionError): ExecutionLifecycle.transition(ExecutionStatus.CREATED, ExecutionStatus.COMPLETED)

    def test_permission_checks_and_context_do_not_accept_credentials(self):
        with self.assertRaises(PermissionDeniedError):
            from agent_foundation.permissions import require_permissions
            require_permissions(set(), {Permission.REPOSITORY_READ})
        with self.assertRaises(ValueError): self.context(github_token="secret")

    def test_safe_paths_and_evidence_are_snapshot_bound(self):
        for path in ("../secret", "/etc/passwd", "C:/private", "a\\b"):
            with self.assertRaises(ValueError): validate_relative_path(path)
        ref = EvidenceReference(evidence_id="e1", evidence_type=EvidenceType.FILE, snapshot_id=uuid.uuid4(),
            manifest_digest="b"*64, path="src/app.py", start_line=2, end_line=3)
        finding = Finding(finding_id="f1", category=FindingCategory.QUALITY, title="Issue", description="Observed",
            severity=Severity.LOW, confidence=.8, evidence=(ref,), verification_status=VerificationStatus.UNVERIFIED)
        self.assertEqual(finding.verification_status, VerificationStatus.UNVERIFIED)

    def test_tool_validates_permission_and_result(self):
        class ToolInput(BaseModel):
            model_config = ConfigDict(extra="forbid")
            value: int
        class ToolOutput(BaseModel):
            model_config = ConfigDict(extra="forbid")
            value: int
        contract = ToolContract(tool_id="shared.echo", name="Echo", description="test", input_schema=ToolInput.model_json_schema(),
            output_schema=ToolOutput.model_json_schema(), required_permissions={"analysis.run"}, category=ToolCategory.SHARED_PLATFORM,
            timeout_seconds=1)
        tools = ToolRegistry()
        tools.register(ToolDefinition(contract, ToolInput, ToolOutput, lambda args, context: {"value": args.value}))
        ctx = self.context()
        result = invoke_tool(tools, "shared.echo", {"value": 3}, context=ctx)
        self.assertEqual(result.output.value, 3)

    def test_gateway_adapter_fails_closed_without_gateway(self):
        model = GatewayChatModel(gateway=None, permissions={Permission.LLM_INVOKE}, agent_id="review_agent",
            execution_id=uuid.uuid4(), correlation_id="c", trace_id="t")
        from agent_foundation.errors import LLMGatewayUnavailableError
        with self.assertRaises(LLMGatewayUnavailableError): model.invoke("prompt", output_schema={})

    def test_native_langgraph_state_graph(self):
        graph = StateGraph(dict)
        graph.add_node("one", lambda state: {"count": state.get("count", 0) + 1})
        graph.add_node("two", lambda state: {"count": state["count"] + 1})
        graph.add_edge(START, "one")
        graph.add_edge("one", "two")
        graph.add_edge("two", END)
        result = graph.compile().invoke({"count": 0})
        self.assertEqual(result["count"], 2)
