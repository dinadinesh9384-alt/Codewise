"""Native LangGraph orchestration over explicitly registered Agent Foundation runners."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_foundation.contracts.execution import ExecutionRequest, ExecutionStatus
from agent_foundation.context import ExecutionContext
from agent_foundation.execution import ExecutionLifecycle
from agent_foundation.permissions import Permission
from agent_foundation.registry import AgentRegistry

from .contracts import GROUPS, OFFICIAL, AgentWorkflowResult, WorkflowRequest, WorkflowResult
from .checkpoint_capability import CheckpointCapability
from .state import WorkflowState

MAX_RESULT_BYTES = 1_000_000
_FORBIDDEN_PERMISSION = {Permission.PATCH_APPLY, Permission.GITHUB_PUBLISH}


@dataclass(frozen=True)
class AgentNodeBinding:
    """Trusted server-side factories; never serialized into graph/checkpoint state."""
    input_factory: Callable[[WorkflowRequest, dict[str, dict]], object]
    context_factory: Callable[[WorkflowRequest, str, uuid.UUID], ExecutionContext]
    input_factory_with_state: Callable[[WorkflowRequest, dict[str, dict], dict[str, str]], object] | None = None


@dataclass(frozen=True)
class WorkflowGraph:
    compiled: object
    has_checkpointer: bool
    can_produce_patch: bool
    can_approve: bool
    durable_checkpointer: bool = False

    def invoke(self, initial_state, *, config=None):
        return self.compiled.invoke(initial_state, config=config)

    def stream(self, initial_state, *, config=None):
        return self.compiled.stream(initial_state, config=config)

    def resume(self, decision, *, config):
        if not self.has_checkpointer:
            raise ValueError("Resuming human approval requires a configured checkpoint saver.")
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected.")
        return self.compiled.invoke(Command(resume=decision), config=config)


def _json_output(output):
    if hasattr(output, "model_dump"):
        value = output.model_dump(mode="json")
    else:
        value = output
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError("Agent output exceeds orchestration state limit.")
    return json.loads(encoded)


def _merge_state_result(state, result):
    results, statuses, errors = {}, {}, {}
    status = result.status.value
    statuses[result.agent_id] = status
    if result.output is not None:
        results[result.agent_id] = _json_output(result.output)
    if result.errors:
        errors[result.agent_id] = ",".join(error.code for error in result.errors)
    return {"agent_results": results, "statuses": statuses, "errors": errors,
            "events": [{"agent_id": result.agent_id, "status": status,
                        "execution_id": str(result.execution_id), "trace_id": result.trace_id}]}


def build_workflow(registry: AgentRegistry, lifecycle: ExecutionLifecycle, bindings: dict[str, AgentNodeBinding],
                   *, checkpointer=None, checkpoint_capability: CheckpointCapability | None = None,
                   runtime_id: uuid.UUID | None = None, observer=None) -> WorkflowGraph:
    """Compile a real LangGraph StateGraph. No placeholders are registered for missing agents."""
    definitions = {agent_id: registry.resolve(agent_id) for agent_id in OFFICIAL
                   if agent_id in bindings and any(c.agent_id == agent_id for c in registry.metadata())}
    for agent_id, definition in definitions.items():
        if _FORBIDDEN_PERMISSION.intersection(definition.contract.required_permissions):
            raise ValueError(f"Agent {agent_id} requests a forbidden workflow permission.")
        if agent_id == "structure_analyzer" and definition.contract.requires_llm:
            raise ValueError("structure_analyzer must remain deterministic and LLM-free")

    graph = StateGraph(WorkflowState)

    def preflight(state: WorkflowState):
        statuses, errors = {}, {}
        for agent_id in OFFICIAL:
            if agent_id not in definitions:
                statuses[agent_id] = "unavailable"
                errors[agent_id] = "agent_not_registered"
        event = {"stage": "preflight", "selected_agent_count": sum(bool(v) for v in state["selected"].values()),
                 "registered_agent_count": len(definitions)}
        return {"statuses": statuses, "errors": errors, "events": [event]}

    graph.add_node("preflight", preflight)

    def agent_node(agent_id):
        def run(state: WorkflowState):
            selected = state["selected"].get(agent_id, False)
            if not selected:
                return {"statuses": {agent_id: "skipped"}, "events": [{"agent_id": agent_id, "status": "skipped"}]}
            dependencies = ()
            if agent_id == "business_logic_analyst":
                dependencies = ("structure_analyzer",)
            elif agent_id in {"code_quality_analyst", "security_analyst", "migration_planner"}:
                dependencies = ("structure_analyzer", "business_logic_analyst")
                if agent_id == "migration_planner":
                    dependencies += tuple(a for a in ("code_quality_analyst", "security_analyst")
                                          if state["selected"].get(a, False))
            elif agent_id == "patch_author":
                dependencies = ("structure_analyzer", "business_logic_analyst") + tuple(
                    a for a in ("code_quality_analyst", "security_analyst", "migration_planner")
                                     if state["selected"].get(a, False))
            elif agent_id == "test_engineer":
                dependencies = ("patch_author",)
            elif agent_id == "review_agent":
                # Optional upstream gaps are reviewable context; only mandatory Agents 1 and 2 gate review.
                dependencies = ("structure_analyzer", "business_logic_analyst")
            blocked = [dep for dep in dependencies if state.get("statuses", {}).get(dep) != "completed"]
            if blocked:
                return {"statuses": {agent_id: "skipped"},
                        "errors": {agent_id: "dependency_incomplete"},
                        "events": [{"agent_id": agent_id, "status": "skipped", "dependency_count": len(blocked)}]}
            request = WorkflowRequest(execution_id=state["execution_id"], workspace_id=state["workspace_id"],
                repository_id=state["repository_id"], snapshot_id=state["snapshot_id"], commit_sha=state["commit_sha"],
                manifest_digest=state["manifest_digest"], correlation_id=state["correlation_id"], trace_id=state["trace_id"],
                    agents=frozenset(state["selected_agents"]), groups=frozenset(state["selected_groups"]),
                deadline=datetime.fromisoformat(state["deadline"]) if state.get("deadline") else None)
            execution_id = uuid.uuid5(uuid.UUID(state["execution_id"]), agent_id)
            try:
                binding = bindings[agent_id]
                if binding.input_factory_with_state is not None:
                    input_value = binding.input_factory_with_state(request, state.get("agent_results", {}),
                                                                   state.get("statuses", {}))
                else:
                    input_value = binding.input_factory(request, state.get("agent_results", {}))
                execution = ExecutionRequest(execution_id=execution_id, agent_id=agent_id,
                    agent_version=definitions[agent_id].contract.version, input=input_value,
                    repository_id=request.repository_id, snapshot_id=request.snapshot_id, workspace_id=request.workspace_id,
                    commit_sha=request.commit_sha, manifest_digest=request.manifest_digest,
                    requested_capabilities=frozenset(), correlation_id=request.correlation_id,
                    trace_id=request.trace_id, mode="graph")
                context = bindings[agent_id].context_factory(request, agent_id, execution_id)
                if _FORBIDDEN_PERMISSION.intersection(context.permissions) or Permission.SANDBOX_EXECUTE in context.permissions:
                    return {"statuses": {agent_id: "failed"}, "errors": {agent_id: "forbidden_execution_permission"},
                            "events": [{"agent_id": agent_id, "status": "failed",
                                        "error_code": "forbidden_execution_permission"}]}
                if agent_id == "test_engineer" and Permission.TESTS_EXECUTE in context.permissions:
                    return {"statuses": {agent_id: "failed"}, "errors": {agent_id: "forbidden_test_execution_permission"},
                            "events": [{"agent_id": agent_id, "status": "failed",
                                        "error_code": "forbidden_test_execution_permission"}]}
                if agent_id == "review_agent" and Permission.TESTS_EXECUTE in context.permissions:
                    return {"statuses": {agent_id: "failed"}, "errors": {agent_id: "forbidden_test_execution_permission"},
                            "events": [{"agent_id": agent_id, "status": "failed",
                                        "error_code": "forbidden_test_execution_permission"}]}
                outcome = lifecycle.execute(execution, context)
            except Exception:
                return {"statuses": {agent_id: "failed"}, "errors": {agent_id: "agent_binding_failed"},
                        "events": [{"agent_id": agent_id, "status": "failed", "error_code": "agent_binding_failed"}]}
            if observer:
                try: observer({"execution_id": str(execution_id), "agent_id": agent_id, "status": outcome.status.value,
                               "trace_id": request.trace_id})
                except Exception: pass
            try:
                return _merge_state_result(state, outcome)
            except Exception:
                return {"statuses": {agent_id: "failed"}, "errors": {agent_id: "agent_output_rejected"},
                        "events": [{"agent_id": agent_id, "status": "failed", "error_code": "agent_output_rejected"}]}
        return run

    ordered = OFFICIAL
    for agent_id in ordered:
        if agent_id in definitions:
            graph.add_node(agent_id, agent_node(agent_id))

    graph.add_node("mandatory_join", lambda state: {})
    graph.add_node("migration_upstream_join", lambda state: {})
    graph.add_node("quality_join", lambda state: {})
    graph.add_node("approval_gate", lambda state: _approval_node(state))
    graph.add_node("finalize", _finalize)
    graph.add_edge(START, "preflight")

    mandatory = [a for a in ("structure_analyzer", "business_logic_analyst") if a in definitions]
    if set(mandatory) == {"structure_analyzer", "business_logic_analyst"}:
        graph.add_edge("preflight", "structure_analyzer")
        graph.add_edge("structure_analyzer", "business_logic_analyst")
        graph.add_edge("business_logic_analyst", "mandatory_join")
    elif mandatory:
        for agent_id in mandatory: graph.add_edge("preflight", agent_id)
        graph.add_edge(mandatory, "mandatory_join")
    else:
        graph.add_edge("preflight", "mandatory_join")
    upstream_quality = [a for a in ("code_quality_analyst", "security_analyst") if a in definitions]
    planner_present = "migration_planner" in definitions
    if planner_present:
        if upstream_quality:
            for agent_id in upstream_quality:
                graph.add_edge("mandatory_join", agent_id)
            graph.add_edge(upstream_quality, "migration_upstream_join")
            graph.add_edge("migration_upstream_join", "migration_planner")
        else:
            graph.add_edge("mandatory_join", "migration_planner")
        graph.add_edge("migration_planner", "quality_join")
        if not upstream_quality:
            pass
    else:
        if upstream_quality:
            for agent_id in upstream_quality:
                graph.add_edge("mandatory_join", agent_id)
            graph.add_edge(upstream_quality, "quality_join")
        else:
            graph.add_edge("mandatory_join", "quality_join")
    if "patch_author" in definitions:
        graph.add_edge("quality_join", "patch_author")
        next_after_patch = "patch_author"
    else:
        next_after_patch = "quality_join"
    if "test_engineer" in definitions:
        graph.add_edge(next_after_patch, "test_engineer")
        next_after_test = "test_engineer"
    else:
        next_after_test = next_after_patch
    if "review_agent" in definitions:
        graph.add_edge(next_after_test, "review_agent")
        graph.add_edge("review_agent", "approval_gate")
    else:
        graph.add_edge(next_after_test, "approval_gate")
    graph.add_edge("approval_gate", "finalize")
    graph.add_edge("finalize", END)
    compiled = graph.compile(checkpointer=checkpointer)
    durable = bool(checkpoint_capability and checkpoint_capability.authorizes(
        checkpointer=checkpointer, runtime_id=runtime_id))
    return WorkflowGraph(compiled=compiled, has_checkpointer=checkpointer is not None,
                         can_produce_patch="patch_author" in definitions,
                         can_approve={"patch_author", "review_agent"}.issubset(definitions),
                         durable_checkpointer=durable)


def _approval_node(state):
    if not state.get("approval_required"):
        return {"approval": "not_required"}
    statuses = state.get("statuses", {})
    results = state.get("agent_results", {})
    if statuses.get("patch_author") != "completed":
        return {"approval": "not_applicable"}
    try:
        from importlib import import_module
        PatchAuthorOutput = import_module("agents.06_patch_author.schemas.output").PatchAuthorOutput
        patch_output = PatchAuthorOutput.model_validate(results.get("patch_author"))
        expected_patch_execution = uuid.uuid5(uuid.UUID(state["execution_id"]), "patch_author")
        if (patch_output.execution_id != expected_patch_execution or
                str(patch_output.repository_id) != state.get("repository_id") or
                str(patch_output.snapshot_id) != state.get("snapshot_id") or
                patch_output.commit_sha != state.get("commit_sha") or
                patch_output.manifest_digest != state.get("manifest_digest") or
                patch_output.status != "awaiting_review" or patch_output.validation_status != "passed" or
                not patch_output.proposals):
            return {"approval": "not_applicable"}
    except Exception:
        return {"approval": "not_applicable"}

    review_selected = bool(state.get("selected", {}).get("review_agent", False))
    if review_selected:
        if statuses.get("review_agent") != "completed":
            return {"approval": "not_applicable"}
        try:
            from importlib import import_module
            ReviewAgentOutput = import_module("agents.08_review_agent.schemas.output").ReviewAgentOutput
            review_output = ReviewAgentOutput.model_validate(results.get("review_agent"))
            expected_review_execution = uuid.uuid5(uuid.UUID(state["execution_id"]), "review_agent")
            if (review_output.execution_id != expected_review_execution or
                    str(review_output.repository_id) != state.get("repository_id") or
                    str(review_output.snapshot_id) != state.get("snapshot_id") or
                    review_output.commit_sha != state.get("commit_sha") or
                    review_output.manifest_digest != state.get("manifest_digest") or
                    review_output.llm_status != "completed" or review_output.validation_errors or
                    review_output.unavailable_agents or review_output.skipped_agents or
                    review_output.failed_agents or review_output.partial_agents or
                    review_output.review_status not in {"ready_for_human_review", "ready_with_warnings"}):
                return {"approval": "not_applicable"}
            expected_upstream = {"structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                                 "security_analyst", "migration_planner", "patch_author", "test_engineer"}
            provenance = {row.agent_id: row for row in review_output.reviewed_agents}
            if set(provenance) != expected_upstream:
                return {"approval": "not_applicable"}
            for agent_id, row in provenance.items():
                expected_status = statuses.get(agent_id, "unavailable")
                expected_selected = bool(state.get("selected", {}).get(agent_id, False))
                expected_present = agent_id in results
                if (row.status != expected_status or row.selected != expected_selected or
                        row.output_present != expected_present):
                    return {"approval": "not_applicable"}
            reviewed_proposals = {row.proposal_id for row in review_output.patch_reviews
                                 if row.validation_status == "verified_in_memory" and not row.applied}
            if not {row.proposal_id for row in patch_output.proposals} <= reviewed_proposals:
                return {"approval": "not_applicable"}
        except Exception:
            return {"approval": "not_applicable"}
    else:
        # This workflow does not allow approval without Agent 8; keep the optional
        # group optional and report that no AI final review was performed.
        return {"approval": "not_applicable", "events": [{"stage": "approval_gate",
                "status": "not_applicable", "reason": "ai_final_review_not_performed"}]}

    readiness = review_output.review_status
    review_disclosure = ("AI final review is ready with warnings; human review is still required."
                         if readiness == "ready_with_warnings" else
                         "AI final review is ready; human review is still required.")
    decision = interrupt({"kind": "human_approval", "execution_id": state["execution_id"],
                          "scope": "reviewed_patch_proposal", "review_status": readiness,
                          "message": review_disclosure})
    if decision not in {"approved", "rejected"}:
        return {"approval": "invalid_decision", "errors": {"approval": "invalid_decision"}}
    return {"approval": decision}


def _finalize(state):
    statuses = state.get("statuses", {})
    selected = state.get("selected", {})
    selected_statuses = [statuses.get(a, "unavailable") for a, yes in selected.items() if yes]
    if selected_statuses and all(s == "completed" for s in selected_statuses):
        status = "completed"
    elif not any(s == "completed" for s in selected_statuses) and "failed" in selected_statuses:
        status = "failed"
    elif "cancelled" in selected_statuses and not any(s == "completed" for s in selected_statuses):
        status = "cancelled"
    elif "timed_out" in selected_statuses and not any(s == "completed" for s in selected_statuses):
        status = "timed_out"
    else:
        status = "partial"
    return {"workflow_status": status,
            "events": [{"stage": "finalize", "status": status, "completed_agents": sum(s == "completed" for s in selected_statuses)}]}


def run_workflow(workflow: WorkflowGraph, request: WorkflowRequest, *, config=None) -> WorkflowResult:
    approval_selected = request.approval_required and workflow.can_approve and request.selected("patch_author") and request.selected("review_agent")
    if approval_selected and not workflow.has_checkpointer:
        raise ValueError("Human approval requires a configured checkpoint saver.")
    if approval_selected and not (config and config.get("configurable", {}).get("thread_id")):
        raise ValueError("Human approval requires a stable LangGraph thread_id configuration.")
    if approval_selected and config["configurable"]["thread_id"] != str(request.execution_id):
        raise ValueError("Human approval checkpoint identity must match the workflow execution.")
    state = {"execution_id": str(request.execution_id), "workspace_id": str(request.workspace_id),
        "repository_id": str(request.repository_id), "snapshot_id": str(request.snapshot_id),
        "commit_sha": request.commit_sha, "manifest_digest": request.manifest_digest,
        "correlation_id": request.correlation_id, "trace_id": request.trace_id,
        "deadline": request.deadline.isoformat() if request.deadline else None,
        "approval_required": request.approval_required,
        "selected_groups": sorted(request.groups), "selected_agents": sorted(request.agents),
        "selected": {agent: request.selected(agent) for agent in OFFICIAL},
        "statuses": {}, "agent_results": {}, "errors": {}, "events": [], "approval": None}
    result = workflow.invoke(state, config=config)
    return _as_workflow_result(request, result)


def resume_workflow(workflow: WorkflowGraph, request: WorkflowRequest, decision: str, *, config) -> WorkflowResult:
    if not request.approval_required:
        raise ValueError("This workflow did not request human approval.")
    if not workflow.can_approve or not request.selected("patch_author") or not request.selected("review_agent"):
        raise ValueError("This workflow has no selected patch and review agents to approve.")
    if not (config and config.get("configurable", {}).get("thread_id")):
        raise ValueError("Resuming human approval requires a stable LangGraph thread_id configuration.")
    if config["configurable"]["thread_id"] != str(request.execution_id):
        raise ValueError("Approval resume checkpoint identity must match the workflow execution.")
    result = workflow.resume(decision, config=config)
    return _as_workflow_result(request, result)


def resume_or_recover_workflow(workflow: WorkflowGraph, request: WorkflowRequest, decision: str, *, config):
    """Apply an outbox decision or continue its already-advanced checkpoint safely."""
    if not request.approval_required or decision not in {"approved", "rejected"}:
        raise ValueError("A valid human approval request is required.")
    if not workflow.durable_checkpointer:
        raise ValueError("Approval recovery requires a durable PostgreSQL checkpoint.")
    if config.get("configurable", {}).get("thread_id") != str(request.execution_id):
        raise ValueError("Approval recovery checkpoint identity must match the execution.")

    snapshot = workflow.compiled.get_state(config)
    values = snapshot.values or {}
    if (str(values.get("execution_id")) != str(request.execution_id) or
            str(values.get("snapshot_id")) != str(request.snapshot_id) or
            str(values.get("repository_id")) != str(request.repository_id) or
            values.get("commit_sha") != request.commit_sha or
            values.get("manifest_digest") != request.manifest_digest):
        raise ValueError("Approval recovery checkpoint identity does not match the stored execution snapshot.")

    interrupts = [interrupt for task in snapshot.tasks for interrupt in task.interrupts]
    if interrupts:
        if len(interrupts) != 1:
            raise ValueError("Approval recovery found an unexpected checkpoint interrupt state.")
        payload = interrupts[0].value
        if (payload.get("kind") != "human_approval" or
                str(payload.get("execution_id")) != str(request.execution_id)):
            raise ValueError("Approval recovery interrupt belongs to a different execution.")
        return resume_workflow(workflow, request, decision, config=config)

    # The checkpoint may have committed the approval node before the Django
    # transaction failed. Continue only when that same decision is recorded.
    if values.get("approval") != decision:
        raise ValueError("Approval checkpoint is neither pending nor advanced for this decision.")
    continued = workflow.compiled.invoke(None, config=config)
    return _as_workflow_result(request, continued)


def _as_workflow_result(request, result):
    agents = tuple(AgentWorkflowResult(agent_id=agent, status=result.get("statuses", {}).get(agent, "unavailable"),
        output=result.get("agent_results", {}).get(agent),
        error_codes=tuple(filter(None, result.get("errors", {}).get(agent, "").split(",")))) for agent in OFFICIAL)
    paused = bool(result.get("__interrupt__"))
    review_lifecycle = result.get("statuses", {}).get("review_agent", "unavailable")
    review_output = result.get("agent_results", {}).get("review_agent")
    if not request.selected("review_agent"):
        review_status = "not_performed"
    elif review_lifecycle == "failed":
        review_status = "failed"
    elif review_lifecycle == "cancelled":
        review_status = "cancelled"
    elif review_lifecycle == "timed_out":
        review_status = "timed_out"
    elif review_lifecycle == "unavailable":
        review_status = "unavailable"
    elif review_lifecycle == "skipped":
        review_status = ("blocked" if result.get("errors", {}).get("review_agent") == "dependency_incomplete"
                         else "unavailable")
    elif review_lifecycle != "completed" or review_output is None:
        review_status = "incomplete"
    else:
        try:
            from importlib import import_module
            model = import_module("agents.08_review_agent.schemas.output").ReviewAgentOutput
            parsed_review = model.model_validate(review_output)
            expected_execution = uuid.uuid5(request.execution_id, "review_agent")
            if (parsed_review.execution_id != expected_execution or parsed_review.repository_id != request.repository_id or
                    parsed_review.snapshot_id != request.snapshot_id or parsed_review.commit_sha != request.commit_sha or
                    parsed_review.manifest_digest != request.manifest_digest):
                review_status = "failed"
            elif parsed_review.llm_status != "completed" and parsed_review.review_status in {
                    "ready_for_human_review", "ready_with_warnings"}:
                review_status = "incomplete"
            else:
                review_status = parsed_review.review_status
        except Exception:
            review_status = "failed"
    return WorkflowResult(execution_id=request.execution_id,
        status=result.get("workflow_status", "partial") if not paused else "partial", agents=agents,
        errors=tuple(f"{a}:{code}" for a, code in result.get("errors", {}).items()),
        events=tuple(result.get("events", ())), approval="pending" if paused else result.get("approval"),
        final_review_status=review_status)


def in_memory_checkpointer_for_tests():
    """Convenience saver for tests and local development only."""
    return InMemorySaver()
