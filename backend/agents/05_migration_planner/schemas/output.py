from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Severity

from .input import UpstreamFinding


class InventoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: str = Field(pattern=r"^inventory_[0-9a-f]{16}$")
    category: Literal["language", "framework", "build_system", "runtime", "database",
                      "api_contract", "environment", "test", "legacy_indicator", "migration_asset"]
    name: str = Field(min_length=1, max_length=180)
    value: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class DeclaredDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ecosystem: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=180)
    version_spec: str | None = Field(default=None, max_length=150)
    exact_version: str | None = Field(default=None, max_length=80)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)


class InternalDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_module: str = Field(min_length=1, max_length=300)
    target_module: str = Field(min_length=1, max_length=300)
    evidence_id: str = Field(min_length=1, max_length=128)


class MigrationAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    asset_type: Literal["schema_migration", "database_schema", "api_contract", "build_config"]
    evidence_id: str = Field(min_length=1, max_length=128)


class MigrationObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    objective_id: str = Field(pattern=r"^objective_[0-9a-f]{16}$")
    statement: str = Field(min_length=1, max_length=1200)
    status: Literal["caller_provided", "recommendation"]
    rationale: str = Field(min_length=1, max_length=1500)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)
    uncertainty: str = Field(min_length=1, max_length=1000)


class MigrationStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: str = Field(pattern=r"^strategy_[0-9a-f]{16}$")
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1, max_length=2000)
    advantages: tuple[str, ...] = Field(min_length=1, max_length=12)
    tradeoffs: tuple[str, ...] = Field(min_length=1, max_length=12)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    target_assumption: str = Field(min_length=1, max_length=1000)
    recommended: bool = False


class MigrationPhase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    phase_id: str = Field(pattern=r"^phase_[0-9a-f]{16}$")
    order: int = Field(ge=1, le=30)
    title: str = Field(min_length=1, max_length=180)
    purpose: str = Field(min_length=1, max_length=1500)
    depends_on_phase_ids: tuple[str, ...] = Field(default=(), max_length=20)
    task_ids: tuple[str, ...] = Field(min_length=1, max_length=80)
    human_approval_required: bool = False
    approval_reason: str | None = Field(default=None, max_length=1200)


class MigrationTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^migration_task_[0-9a-f]{16}$")
    phase_id: str = Field(pattern=r"^phase_[0-9a-f]{16}$")
    sequence: int = Field(ge=1, le=500)
    title: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=2000)
    priority: int = Field(ge=1, le=5)
    risk: Literal["low", "medium", "high", "critical"]
    implementation_complexity: Literal["small", "medium", "large", "unknown"]
    depends_on: tuple[str, ...] = Field(default=(), max_length=100)
    upstream_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    affected_files: tuple[str, ...] = Field(default=(), max_length=50)
    expected_outcome: str = Field(min_length=1, max_length=1500)
    verification_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)
    breaking_changes: tuple[str, ...] = Field(default=(), max_length=20)
    rollback_strategy: str = Field(min_length=1, max_length=1500)


class MigrationRisk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    risk_id: str = Field(pattern=r"^risk_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2000)
    severity: Severity
    likelihood: Literal["low", "medium", "high", "unknown"]
    mitigation: str = Field(min_length=1, max_length=1500)
    upstream_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    uncertainty: str = Field(min_length=1, max_length=1000)


class DatabaseMigrationConsideration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["evidence_found", "not_detected", "unknown"]
    assessment: str = Field(min_length=1, max_length=2000)
    data_migration_required: Literal["yes", "no", "unknown"]
    compatibility_notes: tuple[str, ...] = Field(default=(), max_length=20)
    rollback_notes: str = Field(min_length=1, max_length=1500)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=30)


class ApiCompatibilityConsideration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["contracts_found", "not_detected", "unknown"]
    assessment: str = Field(min_length=1, max_length=2000)
    breaking_change_risks: tuple[str, ...] = Field(default=(), max_length=20)
    compatibility_tests: tuple[str, ...] = Field(default=(), max_length=20)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=30)


class MigrationPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "migration-planner.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str
    manifest_digest: str
    plan_status: Literal["proposed", "incomplete"] = "incomplete"
    approved: Literal[False] = False
    executed: Literal[False] = False
    target_state: str | None = Field(default=None, max_length=2000)
    current_state_assessment: str = Field(default="", max_length=6000)
    target_state_uncertainty: str = Field(default="", max_length=1500)
    coverage: Literal["complete", "partial"] = "partial"
    scanned_files: int = Field(default=0, ge=0)
    limitations: tuple[str, ...] = ()
    objectives: tuple[MigrationObjective, ...] = Field(default=(), max_length=30)
    strategies: tuple[MigrationStrategy, ...] = Field(default=(), max_length=20)
    selected_strategy_id: str | None = Field(default=None, pattern=r"^strategy_[0-9a-f]{16}$")
    phases: tuple[MigrationPhase, ...] = Field(default=(), max_length=30)
    tasks: tuple[MigrationTask, ...] = Field(default=(), max_length=500)
    risks: tuple[MigrationRisk, ...] = Field(default=(), max_length=100)
    database_consideration: DatabaseMigrationConsideration | None = None
    api_consideration: ApiCompatibilityConsideration | None = None
    inventory_facts: tuple[InventoryFact, ...] = Field(default=(), max_length=1000)
    declared_dependencies: tuple[DeclaredDependency, ...] = Field(default=(), max_length=2000)
    internal_dependencies: tuple[InternalDependency, ...] = Field(default=(), max_length=1500)
    migration_assets: tuple[MigrationAsset, ...] = Field(default=(), max_length=1000)
    upstream_findings: tuple[UpstreamFinding, ...] = Field(default=(), max_length=200)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    uncertainties: tuple[str, ...] = ()
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] = "not_attempted"

    @model_validator(mode="after")
    def plan_references_are_valid(self):
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        if any(item.snapshot_id != self.snapshot_id or item.manifest_digest != self.manifest_digest
               for item in self.evidence):
            raise ValueError("Evidence must belong to this immutable snapshot.")
        evidence_ids = set(evidence_by_id)
        for item in (*self.inventory_facts, *self.declared_dependencies):
            if not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("Inventory references unknown evidence.")
        for item in (*self.internal_dependencies, *self.migration_assets):
            if item.evidence_id not in evidence_ids:
                raise ValueError("Inventory edge references unknown evidence.")
        upstream = {item.finding_id: item for item in self.upstream_findings}
        if len(upstream) != len(self.upstream_findings):
            raise ValueError("Upstream finding IDs must be unique.")
        for finding in self.upstream_findings:
            if any(evidence_by_id.get(ref.evidence_id) != ref for ref in finding.evidence):
                raise ValueError("Upstream finding evidence must be present in the snapshot-bound evidence set.")
        strategy_ids = {item.strategy_id for item in self.strategies}
        if len(strategy_ids) != len(self.strategies):
            raise ValueError("Strategy IDs must be unique.")
        if self.selected_strategy_id is not None and self.selected_strategy_id not in strategy_ids:
            raise ValueError("Selected strategy is unknown.")
        phase_by_id = {item.phase_id: item for item in self.phases}
        if len(phase_by_id) != len(self.phases) or len({item.order for item in self.phases}) != len(self.phases):
            raise ValueError("Phase IDs and ordering must be unique.")
        task_by_id = {item.task_id: item for item in self.tasks}
        if len(task_by_id) != len(self.tasks):
            raise ValueError("Task IDs must be unique.")
        if set(task_by_id) and not phase_by_id:
            raise ValueError("Migration tasks require at least one phase.")
        phase_orders = {key: item.order for key, item in phase_by_id.items()}
        phase_task_ids = [task_id for phase in self.phases for task_id in phase.task_ids]
        if len(phase_task_ids) != len(set(phase_task_ids)) or set(phase_task_ids) != set(task_by_id):
            raise ValueError("Every task must belong to exactly one phase.")
        for phase in self.phases:
            if not set(phase.depends_on_phase_ids) <= set(phase_by_id):
                raise ValueError("Phase depends on an unknown phase.")
            if any(phase_orders[parent] >= phase.order for parent in phase.depends_on_phase_ids):
                raise ValueError("Phase dependencies must precede the dependent phase.")
            if phase.human_approval_required and not phase.approval_reason:
                raise ValueError("Human approval checkpoints require a reason.")
        task_order = {key: item.sequence for key, item in task_by_id.items()}
        for task in self.tasks:
            if task.phase_id not in phase_by_id:
                raise ValueError("Task refers to an unknown phase.")
            if task.task_id in task.depends_on or not set(task.depends_on) <= set(task_by_id):
                raise ValueError("Task dependency refers to itself or an unknown task.")
            if not set(task.upstream_finding_ids) <= set(upstream):
                raise ValueError("Task refers to an unknown upstream finding.")
            if not set(task.evidence_ids) <= evidence_ids:
                raise ValueError("Task refers to unknown evidence.")
            if not set(task.affected_files) <= {item.path for item in self.evidence if item.path}:
                raise ValueError("Task refers to an unsupported repository path.")
            for dependency in task.depends_on:
                parent = task_by_id[dependency]
                if phase_orders[parent.phase_id] > phase_orders[task.phase_id]:
                    raise ValueError("Task depends on work in a later phase.")
                if parent.phase_id == task.phase_id and task_order[dependency] >= task.sequence:
                    raise ValueError("Same-phase task dependencies must precede the dependent task.")
        if _has_cycle({key: item.depends_on for key, item in task_by_id.items()}):
            raise ValueError("Migration task dependency graph contains a cycle.")
        if _has_cycle({key: item.depends_on_phase_ids for key, item in phase_by_id.items()}):
            raise ValueError("Migration phase dependency graph contains a cycle.")
        for item in self.objectives:
            if not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("Objective refers to unknown evidence.")
            if item.status == "caller_provided" and self.target_state is None:
                raise ValueError("Caller-provided objectives require an explicit target state.")
            if item.status == "recommendation" and not item.evidence_ids:
                raise ValueError("Recommended objectives require repository evidence.")
            if item.status == "caller_provided" and item.evidence_ids:
                raise ValueError("Caller-provided objectives cannot be represented as repository evidence.")
        for item in self.strategies:
            if not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("Strategy refers to unknown evidence.")
        for item in self.risks:
            if not set(item.upstream_finding_ids) <= set(upstream) or not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("Risk refers to unknown findings or evidence.")
        if self.database_consideration and not set(self.database_consideration.evidence_ids) <= evidence_ids:
            raise ValueError("Database consideration refers to unknown evidence.")
        if self.api_consideration and not set(self.api_consideration.evidence_ids) <= evidence_ids:
            raise ValueError("API consideration refers to unknown evidence.")
        return self


def _has_cycle(graph):
    indegree = {node: 0 for node in graph}
    children = {node: [] for node in graph}
    for child, parents in graph.items():
        for parent in parents:
            if parent not in graph:
                return True
            indegree[child] += 1
            children[parent].append(child)
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return visited != len(graph)
