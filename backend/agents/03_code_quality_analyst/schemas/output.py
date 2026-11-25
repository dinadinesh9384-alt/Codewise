from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding


class FileQualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=1024)
    lines_of_code: int = Field(ge=0)
    function_count: int = Field(ge=0)
    class_count: int = Field(ge=0)
    average_cyclomatic_complexity: float = Field(ge=1)
    maximum_cyclomatic_complexity: int = Field(ge=1)
    maximum_nesting_depth: int = Field(ge=0)
    missing_public_annotations: int = Field(ge=0)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=100)


class FunctionQualityMetrics(BaseModel):
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
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=10)


class DuplicationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    duplication_id: str = Field(pattern=r"^dup_[0-9a-f]{16}$")
    normalized_block_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    statement_count: int = Field(ge=3)
    occurrences: tuple[EvidenceReference, ...] = Field(min_length=2, max_length=30)


class FindingPriority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(min_length=1, max_length=128)
    priority: int = Field(ge=1, le=5)


class QualityRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^recommendation_[0-9a-f]{16}$")
    action: str = Field(min_length=1, max_length=2000)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    rationale: str = Field(min_length=1, max_length=2000)


class CodeQualityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "code-quality.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str
    manifest_digest: str
    overview: str = Field(default="", max_length=6000)
    coverage: Literal["complete", "partial"] = "partial"
    supported_languages: tuple[str, ...] = ()
    analyzed_files: int = Field(default=0, ge=0)
    skipped_files: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    complexity_metrics: tuple[FileQualityMetrics, ...] = ()
    function_metrics: tuple[FunctionQualityMetrics, ...] = ()
    duplication_groups: tuple[DuplicationGroup, ...] = ()
    findings: tuple[Finding, ...] = ()
    priorities: tuple[FindingPriority, ...] = ()
    maintainability_risk_ids: tuple[str, ...] = ()
    architectural_concern_ids: tuple[str, ...] = ()
    testability_concern_ids: tuple[str, ...] = ()
    recommendations: tuple[QualityRecommendation, ...] = ()
    uncertainties: tuple[str, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] = "not_attempted"

    @model_validator(mode="after")
    def findings_are_linked(self):
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Finding IDs must be unique.")
        known = set(ids)
        if any(item.finding_id not in known for item in self.priorities):
            raise ValueError("Priority refers to an unknown finding.")
        if any(item not in known for item in self.maintainability_risk_ids + self.architectural_concern_ids + self.testability_concern_ids):
            raise ValueError("Finding group contains an unknown finding ID.")
        if any(not set(item.finding_ids) <= known for item in self.recommendations):
            raise ValueError("Recommendation refers to an unknown finding.")
        return self
