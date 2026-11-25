from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference

ReviewAgentId = Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst",
    "security_analyst", "migration_planner", "patch_author", "test_engineer"]
LifecycleStatus = Literal["completed", "skipped", "failed", "timed_out", "cancelled", "unavailable"]


class UpstreamReviewStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: ReviewAgentId
    status: LifecycleStatus
    selected: bool
    output_present: bool = False
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] | None = None
    analysis_status: str | None = Field(default=None, max_length=40)
    validation_status: str | None = Field(default=None, max_length=40)


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst"]
    finding_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2000)
    severity: str = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=12)
    related_files: tuple[str, ...] = Field(default=(), max_length=30)
    classification: str | None = Field(default=None, max_length=80)
    remediation: str | None = Field(default=None, max_length=1200)


class ReviewFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: ReviewAgentId
    item_id: str | None = Field(default=None, max_length=128)
    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=240)
    details: str = Field(min_length=1, max_length=1500)
    related_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)
    status: str | None = Field(default=None, max_length=40)


class ReviewTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=1200)
    priority: int = Field(ge=1, le=5)
    risk: str = Field(min_length=1, max_length=20)
    depends_on: tuple[str, ...] = Field(default=(), max_length=50)
    finding_ids: tuple[str, ...] = Field(default=(), max_length=30)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    expected_outcome: str = Field(min_length=1, max_length=1000)
    affected_files: tuple[str, ...] = Field(default=(), max_length=30)


class ReviewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    validation_status: str = Field(min_length=1, max_length=40)
    proposal_status: str = Field(min_length=1, max_length=40)
    original_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    diff: str = Field(min_length=1, max_length=200000)
    source_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    migration_task_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=1200)
    rationale: str = Field(min_length=1, max_length=1200)
    required_tests: tuple[str, ...] = Field(default=(), max_length=20)
    risk: str = Field(min_length=1, max_length=20)
    independently_validated: bool = False


class ReviewScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario_id: str = Field(min_length=1, max_length=128)
    scenario_type: str = Field(min_length=1, max_length=30)
    test_level: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=240)
    priority: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=1200)
    preconditions: tuple[str, ...] = Field(default=(), max_length=15)
    inputs: tuple[str, ...] = Field(default=(), max_length=20)
    steps: tuple[str, ...] = Field(min_length=1, max_length=20)
    related_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    migration_task_ids: tuple[str, ...] = Field(default=(), max_length=50)
    patch_proposal_ids: tuple[str, ...] = Field(default=(), max_length=30)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    expected_results: tuple[str, ...] = Field(min_length=1, max_length=20)
    framework: str | None = Field(default=None, max_length=80)
    execution_status: Literal["not_executed"] = "not_executed"
    claim_validation: str = Field(default="references_validated_semantics_unverified", max_length=60)


class RepositoryReviewContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    file_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    files_inspected: int = Field(ge=0)
    files_skipped: int = Field(ge=0)
    completion_status: str = Field(min_length=1, max_length=40)
    languages: tuple[str, ...] = Field(default=(), max_length=30)
    technologies: tuple[str, ...] = Field(default=(), max_length=60)
    entry_points: tuple[str, ...] = Field(default=(), max_length=60)
    configurations: tuple[str, ...] = Field(default=(), max_length=100)
    dependencies: tuple[str, ...] = Field(default=(), max_length=200)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)


class ReviewAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_statuses: tuple[UpstreamReviewStatus, ...] = Field(min_length=7, max_length=7)
    repository: RepositoryReviewContext | None = None
    findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=500)
    facts: tuple[ReviewFact, ...] = Field(default=(), max_length=700)
    migration_tasks: tuple[ReviewTask, ...] = Field(default=(), max_length=500)
    patch_proposals: tuple[ReviewPatch, ...] = Field(default=(), max_length=30)
    test_scenarios: tuple[ReviewScenario, ...] = Field(default=(), max_length=200)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=100)
    limitations: tuple[str, ...] = Field(default=(), max_length=100)
    upstream_truncated: bool = False

    @model_validator(mode="after")
    def review_context_is_bound(self):
        expected = {"structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst",
                    "migration_planner", "patch_author", "test_engineer"}
        statuses = {row.agent_id: row for row in self.upstream_statuses}
        if set(statuses) != expected:
            raise ValueError("Every upstream agent must have exactly one lifecycle status.")
        if any(not row.selected and row.output_present for row in self.upstream_statuses):
            raise ValueError("Unselected upstream output cannot be consumed.")
        evidence_by_id = {row.evidence_id: row for row in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        if any(row.snapshot_id != self.snapshot_id or row.manifest_digest != self.manifest_digest
               for row in self.evidence):
            raise ValueError("Evidence must match the requested repository snapshot.")
        finding_ids = {row.finding_id for row in self.findings}
        task_ids = {row.task_id for row in self.migration_tasks}
        patch_ids = {row.proposal_id for row in self.patch_proposals}
        scenario_ids = {row.scenario_id for row in self.test_scenarios}
        if len(finding_ids) != len(self.findings) or len(task_ids) != len(self.migration_tasks):
            raise ValueError("Upstream finding and migration task IDs must be unique.")
        if len(patch_ids) != len(self.patch_proposals) or len(scenario_ids) != len(self.test_scenarios):
            raise ValueError("Patch and scenario IDs must be unique.")
        for item in self.findings:
            if not set(item.evidence_ids) <= evidence_by_id.keys():
                raise ValueError("Finding references unknown evidence.")
        for item in self.facts:
            if not set(item.evidence_ids) <= evidence_by_id.keys():
                raise ValueError("Upstream fact references unknown evidence.")
        known_items = finding_ids | task_ids | patch_ids | scenario_ids | {
            row.item_id for row in self.facts if row.item_id}
        if any(not set(item.related_ids) <= known_items for item in self.facts):
            raise ValueError("Upstream fact references an unknown stable ID.")
        for task in self.migration_tasks:
            if (not set(task.evidence_ids) <= evidence_by_id.keys() or
                    not set(task.finding_ids) <= finding_ids or not set(task.depends_on) <= task_ids):
                raise ValueError("Migration task references unknown findings, tasks, or evidence.")
        for patch in self.patch_proposals:
            if (not set(patch.source_finding_ids) <= finding_ids or
                    not set(patch.migration_task_ids) <= task_ids or
                    not set(patch.evidence_ids) <= evidence_by_id.keys()):
                raise ValueError("Patch proposal references unknown findings, tasks, or evidence.")
            if (patch.path not in {evidence_by_id[eid].path for eid in patch.evidence_ids} or
                    patch.original_content_hash not in {evidence_by_id[eid].content_hash for eid in patch.evidence_ids}):
                raise ValueError("Patch proposal path and base hash must match its snapshot evidence.")
        for scenario in self.test_scenarios:
            if (not set(scenario.related_finding_ids) <= finding_ids or
                    not set(scenario.migration_task_ids) <= task_ids or
                    not set(scenario.patch_proposal_ids) <= patch_ids or
                    not set(scenario.evidence_ids) <= evidence_by_id.keys()):
                raise ValueError("Test scenario references unknown findings, tasks, patches, or evidence.")
        return self
