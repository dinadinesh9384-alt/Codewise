from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference

from .input import MigrationTaskReference, PatchProposalReference, TestFinding, UpstreamStatus


class TestFileInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    language: str = Field(min_length=1, max_length=40)
    frameworks: tuple[str, ...] = ()
    test_case_count: int = Field(default=0, ge=0)
    fixture_count: int = Field(default=0, ge=0)
    assertion_count: int = Field(default=0, ge=0)
    mock_indicators: tuple[str, ...] = ()
    boundary_indicators: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    analysis_status: Literal["analyzed", "recognized_unparsed", "unreadable"]


class CoverageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    report_path: str = Field(min_length=1, max_length=1024)
    report_format: Literal["coverage_json", "coverage_xml"]
    covered_lines: int = Field(ge=0)
    valid_lines: int = Field(ge=1)
    percent_covered: float = Field(ge=0, le=100)
    measured: Literal[True] = True
    provenance: Literal["snapshot_report_only"] = "snapshot_report_only"
    independently_verified: Literal[False] = False
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=5)


class TestConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    kind: Literal["framework_config", "dependency_manifest", "coverage_config", "test_fixture"]
    observed_frameworks: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)


class PatchAnalysisObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str = Field(pattern=r"^patch_[0-9a-f]{16}$")
    analysis_status: Literal["verified", "stale", "unsupported", "not_analyzed"]
    affected_path: str = Field(min_length=1, max_length=1024)
    changed_symbols: tuple[str, ...] = Field(default=(), max_length=100)
    affected_test_files: tuple[str, ...] = Field(default=(), max_length=100)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    assessment: str = Field(min_length=1, max_length=1800)


class TestScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario_id: str = Field(pattern=r"^test_scenario_[0-9a-f]{16}$")
    scenario_type: Literal["positive", "negative", "boundary", "regression", "security", "compatibility"]
    test_level: Literal["unit", "integration", "api", "system", "static"]
    title: str = Field(min_length=1, max_length=240)
    priority: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=2000)
    related_finding_ids: tuple[str, ...] = Field(default=(), max_length=50)
    migration_task_ids: tuple[str, ...] = Field(default=(), max_length=50)
    patch_proposal_ids: tuple[str, ...] = Field(default=(), max_length=30)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    preconditions: tuple[str, ...] = Field(min_length=1, max_length=20)
    inputs: tuple[str, ...] = Field(min_length=1, max_length=30)
    steps: tuple[str, ...] = Field(min_length=1, max_length=20)
    expected_results: tuple[str, ...] = Field(min_length=1, max_length=20)
    framework: str | None = Field(default=None, max_length=80)
    uncertainty: str = Field(min_length=1, max_length=1200)
    execution_status: Literal["not_executed"] = "not_executed"
    claim_validation: Literal["references_validated_semantics_unverified"] = "references_validated_semantics_unverified"


class TestCodeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str = Field(pattern=r"^test_code_[0-9a-f]{16}$")
    target_path: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=1, max_length=40_000)
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    related_scenario_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    patch_proposal_ids: tuple[str, ...] = Field(default=(), max_length=30)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    status: Literal["awaiting_review"] = "awaiting_review"
    execution_status: Literal["not_executed"] = "not_executed"
    assumptions: tuple[str, ...] = Field(min_length=1, max_length=20)


class TestEngineerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "test-engineer.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["proposed", "partial", "abstained", "unavailable", "rejected"]
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"]
    execution_status: Literal["not_executed"] = "not_executed"
    summary: str = Field(default="", max_length=6000)
    summary_claim_validation: Literal["references_validated_semantics_unverified"] = "references_validated_semantics_unverified"
    upstream_statuses: tuple[UpstreamStatus, ...] = Field(min_length=6, max_length=6)
    source_findings: tuple[TestFinding, ...] = Field(default=(), max_length=400)
    migration_tasks: tuple[MigrationTaskReference, ...] = Field(default=(), max_length=500)
    patch_status: Literal["awaiting_review", "abstained", "unavailable", "rejected", "missing"]
    patch_proposals: tuple[PatchProposalReference, ...] = Field(default=(), max_length=30)
    patch_analyses: tuple[PatchAnalysisObservation, ...] = Field(default=(), max_length=30)
    test_files: tuple[TestFileInventory, ...] = Field(default=(), max_length=2500)
    test_configurations: tuple[TestConfiguration, ...] = Field(default=(), max_length=200)
    coverage_observations: tuple[CoverageObservation, ...] = Field(default=(), max_length=50)
    scenarios: tuple[TestScenario, ...] = Field(default=(), max_length=200)
    test_code_proposals: tuple[TestCodeProposal, ...] = Field(default=(), max_length=20)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    missing_upstream_agents: tuple[str, ...] = Field(default=(), max_length=6)
    skipped_upstream_agents: tuple[str, ...] = Field(default=(), max_length=6)
    failed_upstream_agents: tuple[str, ...] = Field(default=(), max_length=6)
    unavailable_upstream_agents: tuple[str, ...] = Field(default=(), max_length=6)
    partial_upstream_agents: tuple[str, ...] = Field(default=(), max_length=6)
    limitations: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()

    @model_validator(mode="after")
    def output_references_are_bound(self):
        evidence_by_id = {ref.evidence_id: ref for ref in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        if any(ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest for ref in self.evidence):
            raise ValueError("Evidence must belong to the authorized snapshot.")
        status_agents = {row.agent_id for row in self.upstream_statuses}
        if len(status_agents) != len(self.upstream_statuses):
            raise ValueError("Every upstream lifecycle status must be represented once.")
        for field_name in ("missing_upstream_agents", "skipped_upstream_agents", "failed_upstream_agents",
                           "unavailable_upstream_agents", "partial_upstream_agents"):
            if not set(getattr(self, field_name)) <= status_agents:
                raise ValueError("Upstream status summary references an unknown agent.")
        for finding in self.source_findings:
            if any(evidence_by_id.get(ref.evidence_id) != ref for ref in finding.evidence):
                raise ValueError("Output findings must use the snapshot-bound evidence index.")
        finding_ids = {row.finding_id for row in self.source_findings}
        task_ids = {row.task_id for row in self.migration_tasks}
        patch_by_id = {row.proposal_id: row for row in self.patch_proposals}
        if len(patch_by_id) != len(self.patch_proposals):
            raise ValueError("Patch proposal IDs must be unique.")
        scenario_ids = {row.scenario_id for row in self.scenarios}
        patch_analyses = {row.proposal_id: row for row in self.patch_analyses}
        if len(patch_analyses) != len(self.patch_analyses) or set(patch_analyses) != set(patch_by_id):
            raise ValueError("Every upstream patch proposal requires exactly one independent analysis result.")
        for analysis in self.patch_analyses:
            if (analysis.affected_path != patch_by_id[analysis.proposal_id].path or
                    not set(analysis.evidence_ids) <= set(evidence_by_id) or
                    not set(analysis.affected_test_files) <= {item.path for item in self.test_files}):
                raise ValueError("Patch analysis references an unsupported path or evidence.")
        if len(scenario_ids) != len(self.scenarios):
            raise ValueError("Scenario IDs must be unique.")
        for scenario in self.scenarios:
            if (not set(scenario.related_finding_ids) <= finding_ids or
                    not set(scenario.migration_task_ids) <= task_ids or
                    not set(scenario.patch_proposal_ids) <= set(patch_by_id) or
                    not set(scenario.evidence_ids) <= set(evidence_by_id)):
                raise ValueError("Scenario references unknown findings, tasks, patches, or evidence.")
            if scenario.patch_proposal_ids and self.patch_status != "awaiting_review":
                raise ValueError("Scenario cannot cite a patch that is not awaiting review.")
            if any(patch_analyses[proposal_id].analysis_status != "verified"
                   for proposal_id in scenario.patch_proposal_ids):
                raise ValueError("Scenario cannot claim analysis of a stale or unsupported patch.")
        for inventory in self.test_files:
            if not set(inventory.evidence_ids) <= set(evidence_by_id):
                raise ValueError("Test inventory references unknown evidence.")
        for config in self.test_configurations:
            if not set(config.evidence_ids) <= set(evidence_by_id):
                raise ValueError("Test configuration references unknown evidence.")
        for coverage in self.coverage_observations:
            if (not set(coverage.evidence_ids) <= set(evidence_by_id) or
                    evidence_by_id[coverage.evidence_ids[0]].path != coverage.report_path):
                raise ValueError("Coverage report must be snapshot-bound to its report file.")
        for artifact in self.test_code_proposals:
            if (artifact.target_path not in {row.path for row in self.test_files} or
                    not set(artifact.related_scenario_ids) <= scenario_ids or
                    not set(artifact.patch_proposal_ids) <= set(patch_by_id) or
                    not set(artifact.evidence_ids) <= set(evidence_by_id)):
                raise ValueError("Test-code proposal references unknown paths, scenarios, patches, or evidence.")
            if not set(artifact.patch_proposal_ids) <= {key for key, value in patch_analyses.items()
                                                       if value.analysis_status == "verified"}:
                raise ValueError("Test-code proposal references a patch that was not independently verified.")
        if self.status == "proposed" and not self.scenarios:
            raise ValueError("A proposed test plan requires at least one scenario.")
        return self

    @property
    def findings(self):
        """Upstream findings are retained as provenance, not re-emitted as Agent 7 findings."""
        return ()
