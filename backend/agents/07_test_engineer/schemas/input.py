from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding, FindingCategory

TestUpstreamAgent = Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst",
    "security_analyst", "migration_planner", "patch_author"]
StatusValue = Literal["completed", "skipped", "failed", "timed_out", "cancelled", "unavailable"]


class TestEngineerOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=500, ge=1, le=2500, strict=True)
    max_file_size_bytes: int = Field(default=128_000, ge=1, le=256_000, strict=True)
    max_total_content_bytes: int = Field(default=2_000_000, ge=1, le=4_000_000, strict=True)
    max_prompt_bytes: int = Field(default=90_000, ge=2_000, le=180_000, strict=True)
    max_output_tokens: int = Field(default=4000, ge=128, le=8000, strict=True)
    max_scenarios: int = Field(default=80, ge=1, le=200, strict=True)
    max_code_proposals: int = Field(default=8, ge=0, le=20, strict=True)


class UpstreamStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: TestUpstreamAgent
    status: StatusValue
    selected: bool = False
    output_present: bool = False
    context_truncated: bool = False
    execution_status: Literal["not_started", "completed", "skipped", "failed", "timed_out", "cancelled", "unavailable", "missing_output"] = "not_started"
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] | None = None
    analysis_status: str | None = Field(default=None, max_length=40)
    validation_status: str | None = Field(default=None, max_length=40)
    output_status: str | None = Field(default=None, max_length=40)


class BusinessWorkflowContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{16}$")
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1600)
    steps: tuple[str, ...] = Field(default=(), max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class BusinessClaimContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1200)
    reasoning: Literal["direct", "interpretation", "inference", "unverified_hypothesis"]
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    uncertainty: str | None = Field(default=None, max_length=800)


class BusinessRelationshipContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relationship_id: str = Field(pattern=r"^relationship_[0-9a-f]{16}$")
    source_entity: str = Field(min_length=1, max_length=180)
    target_entity: str = Field(min_length=1, max_length=180)
    relationship: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)


class BusinessContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    executive_overview: str = Field(default="", max_length=2500)
    domain_summary: str = Field(default="", max_length=2500)
    workflows: tuple[BusinessWorkflowContext, ...] = Field(default=(), max_length=30)
    business_rules: tuple[BusinessClaimContext, ...] = Field(default=(), max_length=40)
    entities: tuple[BusinessClaimContext, ...] = Field(default=(), max_length=40)
    relationships: tuple[BusinessRelationshipContext, ...] = Field(default=(), max_length=40)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=30)


class QualityMetricContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    function_count: int = Field(ge=0)
    class_count: int = Field(ge=0)
    average_cyclomatic_complexity: float = Field(ge=1)
    maximum_cyclomatic_complexity: int = Field(ge=1)
    maximum_nesting_depth: int = Field(ge=0)
    missing_public_annotations: int = Field(ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)


class QualityRecommendationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^recommendation_[0-9a-f]{16}$")
    action: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class QualityFunctionMetricContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    qualified_name: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    length: int = Field(ge=1)
    cyclomatic_complexity: int = Field(ge=1)
    nesting_depth: int = Field(ge=0)
    parameter_count: int = Field(ge=0)
    missing_annotations: int = Field(ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)


class QualityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    overview: str = Field(default="", max_length=2500)
    analysis_coverage: Literal["complete", "partial"] = "partial"
    metrics: tuple[QualityMetricContext, ...] = Field(default=(), max_length=60)
    function_metrics: tuple[QualityFunctionMetricContext, ...] = Field(default=(), max_length=120)
    recommendations: tuple[QualityRecommendationContext, ...] = Field(default=(), max_length=30)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=30)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)


class SecurityAssessmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(pattern=r"^security_[0-9a-f]{16}$")
    assessment: Literal["confirmed_pattern", "potential_risk", "likely_not_issue"]
    rationale: str = Field(min_length=1, max_length=1000)
    uncertainty: str = Field(min_length=1, max_length=800)


class SecurityInsightContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    insight_id: str = Field(pattern=r"^insight_[0-9a-f]{16}$")
    kind: Literal["attack_path", "missing_control", "cross_file_risk"]
    title: str = Field(min_length=1, max_length=240)
    explanation: str = Field(min_length=1, max_length=1200)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    uncertainty: str = Field(min_length=1, max_length=800)


class SecurityRecommendationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^security_recommendation_[0-9a-f]{16}$")
    action: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class SecurityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    overview: str = Field(default="", max_length=2000)
    analysis_coverage: Literal["complete", "partial"] = "partial"
    scanned_files: int = Field(default=0, ge=0)
    assessments: tuple[SecurityAssessmentContext, ...] = Field(default=(), max_length=100)
    insights: tuple[SecurityInsightContext, ...] = Field(default=(), max_length=40)
    recommendations: tuple[SecurityRecommendationContext, ...] = Field(default=(), max_length=40)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=30)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)


class MigrationRiskContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    risk_id: str = Field(pattern=r"^risk_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1200)
    severity: str = Field(min_length=1, max_length=20)
    likelihood: str = Field(min_length=1, max_length=20)
    upstream_finding_ids: tuple[str, ...] = Field(default=(), max_length=30)
    mitigation: str = Field(min_length=1, max_length=1000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    uncertainty: str = Field(min_length=1, max_length=800)


class DeclaredDependencyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ecosystem: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=180)
    version_spec: str | None = Field(default=None, max_length=150)
    exact_version: str | None = Field(default=None, max_length=80)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)


class InternalDependencyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_module: str = Field(min_length=1, max_length=300)
    target_module: str = Field(min_length=1, max_length=300)
    evidence_id: str = Field(min_length=1, max_length=128)


class MigrationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_status: Literal["proposed", "incomplete"] = "incomplete"
    current_state_assessment: str = Field(default="", max_length=2500)
    target_state: str | None = Field(default=None, max_length=1000)
    target_state_uncertainty: str = Field(default="", max_length=800)
    risks: tuple[MigrationRiskContext, ...] = Field(default=(), max_length=50)
    declared_dependencies: tuple[DeclaredDependencyContext, ...] = Field(default=(), max_length=100)
    internal_dependencies: tuple[InternalDependencyContext, ...] = Field(default=(), max_length=100)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    database_assessment: str | None = Field(default=None, max_length=1000)
    database_data_migration_required: Literal["yes", "no", "unknown"] | None = None
    api_assessment: str | None = Field(default=None, max_length=1000)
    api_breaking_change_risks: tuple[str, ...] = Field(default=(), max_length=20)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=30)
    limitations: tuple[str, ...] = Field(default=(), max_length=30)


class PatchSourceFindingContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(min_length=1, max_length=128)
    agent_id: Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst"]
    title: str = Field(min_length=1, max_length=240)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=12)


class PatchContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repository_id: UUID | None = None
    snapshot_id: UUID | None = None
    commit_sha: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] | None = None
    validation_status: Literal["passed", "not_run", "rejected"] | None = None
    proposal_status: Literal["awaiting_review", "abstained", "unavailable", "rejected", "missing"] = "missing"
    summary: str = Field(default="", max_length=2000)
    source_findings: tuple[PatchSourceFindingContext, ...] = Field(default=(), max_length=100)
    affected_paths: tuple[str, ...] = Field(default=(), max_length=100)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=30)


class TestFinding(Finding):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst"]
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=12)


class MigrationTaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^migration_task_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    affected_files: tuple[str, ...] = Field(default=(), max_length=50)
    priority: int | None = Field(default=None, ge=1, le=5)
    risk: Literal["low", "medium", "high", "critical"] | None = None
    depends_on: tuple[str, ...] = Field(default=(), max_length=50)
    upstream_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    expected_outcome: str | None = Field(default=None, max_length=1000)
    verification_criteria: tuple[str, ...] = Field(default=(), max_length=20)
    breaking_changes: tuple[str, ...] = Field(default=(), max_length=20)
    rollback_strategy: str | None = Field(default=None, max_length=1000)
    phase_id: str | None = Field(default=None, pattern=r"^phase_[0-9a-f]{16}$")
    sequence: int | None = Field(default=None, ge=1, le=500)


class PatchProposalReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str = Field(pattern=r"^patch_[0-9a-f]{16}$")
    status: Literal["awaiting_review"]
    validation_status: Literal["passed"]
    path: str = Field(min_length=1, max_length=1024)
    original_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    unified_diff: str = Field(min_length=1, max_length=200_000)
    related_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    migration_task_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_behavior: str = Field(min_length=1, max_length=1500)
    risk: Literal["low", "medium", "high", "critical"]
    compatibility_assessment: str = Field(min_length=1, max_length=1500)
    required_tests: tuple[str, ...] = Field(min_length=1, max_length=20)
    uncertainty: str = Field(min_length=1, max_length=1200)
    change_summary: str = Field(default="", max_length=1200)


class TestEngineerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="test-engineer.input.v1", pattern=r"^test-engineer\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_statuses: tuple[UpstreamStatus, ...] = Field(min_length=6, max_length=6)
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    quality_context: QualityContext = Field(default_factory=QualityContext)
    security_context: SecurityContext = Field(default_factory=SecurityContext)
    migration_context: MigrationContext = Field(default_factory=MigrationContext)
    patch_context: PatchContext = Field(default_factory=PatchContext)
    findings: tuple[TestFinding, ...] = Field(default=(), max_length=400)
    migration_tasks: tuple[MigrationTaskReference, ...] = Field(default=(), max_length=500)
    patch_status: Literal["awaiting_review", "abstained", "unavailable", "rejected", "missing"] = "missing"
    patch_proposals: tuple[PatchProposalReference, ...] = Field(default=(), max_length=30)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    upstream_truncated: bool = False
    options: TestEngineerOptions = Field(default_factory=TestEngineerOptions)

    @model_validator(mode="after")
    def upstream_references_are_bound(self):
        if len({row.agent_id for row in self.upstream_statuses}) != len(self.upstream_statuses):
            raise ValueError("Each upstream agent status must be supplied exactly once.")
        if len({row.finding_id for row in self.findings}) != len(self.findings):
            raise ValueError("Finding IDs must be unique.")
        if len({row.task_id for row in self.migration_tasks}) != len(self.migration_tasks):
            raise ValueError("Migration task IDs must be unique.")
        if len({row.proposal_id for row in self.patch_proposals}) != len(self.patch_proposals):
            raise ValueError("Patch proposal IDs must be unique.")
        evidence_by_id = {row.evidence_id: row for row in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        for ref in self.evidence:
            if ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest:
                raise ValueError("Evidence must belong to the authorized snapshot.")
        def validate_context_evidence(value):
            if isinstance(value, BaseModel):
                validate_context_evidence(value.__dict__)
            elif isinstance(value, dict):
                ids = value.get("evidence_ids", ())
                if not set(ids) <= set(evidence_by_id):
                    raise ValueError("Upstream context references unknown evidence.")
                for nested in value.values():
                    validate_context_evidence(nested)
            elif isinstance(value, (tuple, list)):
                for nested in value:
                    validate_context_evidence(nested)
        for context in (self.business_context, self.quality_context, self.security_context, self.migration_context,
                        self.patch_context):
            validate_context_evidence(context)
        finding_ids = {item.finding_id for item in self.findings}
        if any(not set(row.finding_ids) <= finding_ids for row in self.quality_context.recommendations):
            raise ValueError("Quality recommendation references unknown findings.")
        if any(row.finding_id not in finding_ids for row in self.security_context.assessments):
            raise ValueError("Security assessment references an unknown finding.")
        if any(not set(row.finding_ids) <= finding_ids for row in self.security_context.insights):
            raise ValueError("Security insight references unknown findings.")
        if any(not set(row.finding_ids) <= finding_ids for row in self.security_context.recommendations):
            raise ValueError("Security recommendation references unknown findings.")
        if any(not set(row.upstream_finding_ids) <= finding_ids for row in self.migration_context.risks):
            raise ValueError("Migration risk references unknown findings.")
        if any(not set(row.evidence_ids) <= set(evidence_by_id) for row in self.security_context.recommendations):
            raise ValueError("Security recommendation references unknown evidence.")
        if any(row.evidence_id not in evidence_by_id for row in self.migration_context.internal_dependencies):
            raise ValueError("Internal dependency references unknown evidence.")
        task_ids = {item.task_id for item in self.migration_tasks}
        if any(not set(row.depends_on) <= task_ids for row in self.migration_tasks):
            raise ValueError("Migration task references an unknown dependency.")
        if any(not set(row.upstream_finding_ids) <= finding_ids for row in self.migration_tasks):
            raise ValueError("Migration task references an unknown finding.")
        for item in self.patch_context.source_findings:
            if not set(item.evidence_ids) <= set(evidence_by_id):
                raise ValueError("Patch source finding references unknown evidence.")
            if item.finding_id not in finding_ids:
                raise ValueError("Patch source reference does not match a bound upstream finding.")
        if not set(self.patch_context.affected_paths) <= {ref.path for ref in self.evidence if ref.path}:
            raise ValueError("Patch context references an unsupported path.")
        if self.patch_context.snapshot_id not in {None, self.snapshot_id} or self.patch_context.repository_id not in {None, self.repository_id}:
            raise ValueError("Patch context must match the requested repository snapshot.")
        if self.patch_context.commit_sha not in {None, self.commit_sha} or self.patch_context.manifest_digest not in {None, self.manifest_digest}:
            raise ValueError("Patch context must match the requested repository identity.")
        for finding in self.findings:
            if any(ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest
                   or evidence_by_id.get(ref.evidence_id) != ref for ref in finding.evidence):
                raise ValueError("Finding evidence must be present and bound to the requested snapshot.")
        if any(not set(task.evidence_ids) <= set(evidence_by_id) for task in self.migration_tasks):
            raise ValueError("Migration tasks reference unknown evidence.")
        finding_ids, task_ids = {row.finding_id for row in self.findings}, {row.task_id for row in self.migration_tasks}
        for proposal in self.patch_proposals:
            if (not set(proposal.evidence_ids) <= set(evidence_by_id) or
                    not set(proposal.related_finding_ids) <= finding_ids or
                    not set(proposal.migration_task_ids) <= task_ids):
                raise ValueError("Patch proposal references unknown findings, tasks, or evidence.")
            if any(evidence_by_id[eid].path != proposal.path for eid in proposal.evidence_ids):
                raise ValueError("Patch proposal evidence must support its source path.")
        if self.patch_status != "awaiting_review" and self.patch_proposals:
            raise ValueError("Only an awaiting-review Agent 6 result may contain patch proposals.")
        return self
