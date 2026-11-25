from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding, FindingCategory


class SecurityFinding(Finding):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(pattern=r"^security_[0-9a-f]{16}$")
    category: FindingCategory = FindingCategory.SECURITY
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=4)
    classification: str = Field(min_length=1, max_length=80)
    cwe_id: str | None = Field(default=None, pattern=r"^CWE-[1-9][0-9]{0,5}$")
    remediation: str = Field(min_length=1, max_length=2000)


class SecurityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finding_id: str = Field(pattern=r"^security_[0-9a-f]{16}$")
    assessment: Literal["confirmed_pattern", "potential_risk", "likely_not_issue"]
    rationale: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1, max_length=1500)


class SecurityInsight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    insight_id: str = Field(pattern=r"^insight_[0-9a-f]{16}$")
    kind: Literal["attack_path", "missing_control", "cross_file_risk"]
    title: str = Field(min_length=1, max_length=240)
    explanation: str = Field(min_length=1, max_length=2500)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1, max_length=1500)
    remediation: str = Field(min_length=1, max_length=2000)


class SecurityRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^security_recommendation_[0-9a-f]{16}$")
    action: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    finding_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)


class SecurityAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "security-analysis.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str
    manifest_digest: str
    overview: str = Field(default="", max_length=6000)
    coverage: Literal["complete", "partial"] = "partial"
    supported_languages: tuple[str, ...] = ()
    scanned_files: int = Field(default=0, ge=0)
    skipped_files: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    findings: tuple[SecurityFinding, ...] = Field(default=(), max_length=200)
    assessments: tuple[SecurityAssessment, ...] = ()
    insights: tuple[SecurityInsight, ...] = ()
    recommendations: tuple[SecurityRecommendation, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    uncertainties: tuple[str, ...] = ()
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"] = "not_attempted"

    @model_validator(mode="after")
    def all_references_are_bound(self):
        finding_ids = [item.finding_id for item in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("Security finding IDs must be unique.")
        finding_by_id = {item.finding_id: item for item in self.findings}
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        for evidence in self.evidence:
            if evidence.snapshot_id != self.snapshot_id or evidence.manifest_digest != self.manifest_digest:
                raise ValueError("Evidence must belong to this immutable snapshot.")
        for finding in self.findings:
            if not set(finding.related_files) <= {item.path for item in finding.evidence}:
                raise ValueError("Finding files must be supported by its evidence.")
            if any(evidence_by_id.get(item.evidence_id) != item for item in finding.evidence):
                raise ValueError("Finding evidence must be present in the output evidence set.")
        known = set(finding_ids)
        for assessment in self.assessments:
            if assessment.finding_id not in known:
                raise ValueError("Assessment refers to an unknown finding.")
        for item in (*self.insights, *self.recommendations):
            if not set(item.finding_ids) <= known:
                raise ValueError("Security reasoning refers to an unknown finding.")
            if not set(item.evidence_ids) <= set(evidence_by_id):
                raise ValueError("Security reasoning refers to unknown evidence.")
            linked_evidence = {ref.evidence_id for finding_id in item.finding_ids
                               for ref in finding_by_id[finding_id].evidence}
            if not set(item.evidence_ids) <= linked_evidence:
                raise ValueError("Security reasoning evidence must support its linked findings.")
        return self
