from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from .input import UpstreamReviewStatus

ReviewOutcome = Literal["ready_for_human_review", "ready_with_warnings", "changes_required", "blocked", "incomplete"]


class ReviewAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assessment_type: Literal["model_assessment", "inferred_risk", "unresolved_question", "consistency_check", "corrective_action"]
    topic: Literal["completeness", "evidence_quality", "business_quality_consistency", "security", "migration",
                   "patch", "testing", "cross_agent_conflict", "readiness", "other"]
    statement: str = Field(min_length=1, max_length=1800)
    upstream_ids: tuple[str, ...] = Field(default=(), max_length=50)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)
    uncertainty: str = Field(min_length=1, max_length=800)


class EvidenceValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1, max_length=128)
    validation_status: Literal["hash_verified", "hash_mismatch", "unverifiable", "invalid_reference"]
    detail: str = Field(min_length=1, max_length=400)


class PatchReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str = Field(min_length=1, max_length=128)
    validation_status: Literal["verified_in_memory", "failed", "unavailable"]
    path: str = Field(min_length=1, max_length=1024)
    detail: str = Field(min_length=1, max_length=600)
    applied: Literal[False] = False


class ReviewAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["review-agent.output.v1"] = "review-agent.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_status: ReviewOutcome
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"]
    overall_readiness: ReviewOutcome
    executive_summary: str = Field(min_length=1, max_length=6000)
    executive_summary_upstream_ids: tuple[str, ...] = Field(default=(), max_length=50)
    executive_summary_evidence_ids: tuple[str, ...] = Field(default=(), max_length=20)
    executive_summary_claim_validation: Literal["references_validated_semantics_unverified", "deterministic_fallback"] = "references_validated_semantics_unverified"
    reviewed_agents: tuple[UpstreamReviewStatus, ...] = Field(min_length=7, max_length=7)
    unavailable_agents: tuple[str, ...] = Field(default=(), max_length=7)
    skipped_agents: tuple[str, ...] = Field(default=(), max_length=7)
    failed_agents: tuple[str, ...] = Field(default=(), max_length=7)
    partial_agents: tuple[str, ...] = Field(default=(), max_length=7)
    evidence_supported_findings: tuple[str, ...] = Field(default=(), max_length=500)
    weakly_evidenced_findings: tuple[str, ...] = Field(default=(), max_length=500)
    assessments: tuple[ReviewAssessment, ...] = Field(default=(), max_length=150)
    conflicts: tuple[ReviewAssessment, ...] = Field(default=(), max_length=50)
    evidence_validation: tuple[EvidenceValidation, ...] = Field(default=(), max_length=5000)
    patch_reviews: tuple[PatchReview, ...] = Field(default=(), max_length=30)
    test_scenarios_not_executed: tuple[str, ...] = Field(default=(), max_length=200)
    validation_errors: tuple[str, ...] = Field(default=(), max_length=100)
    uncertainties: tuple[str, ...] = Field(default=(), max_length=100)
    limitations: tuple[str, ...] = Field(default=(), max_length=100)
    human_review_recommendation: Literal["required"] = "required"
    approved_by_human: Literal[False] = False

    @model_validator(mode="after")
    def review_references_are_known(self):
        status_ids = {row.agent_id for row in self.reviewed_agents}
        if len(status_ids) != len(self.reviewed_agents):
            raise ValueError("Reviewed upstream statuses must be unique.")
        if self.review_status != self.overall_readiness:
            raise ValueError("Review outcome and overall readiness must agree.")
        if not (set(self.unavailable_agents) | set(self.skipped_agents) | set(self.failed_agents) |
                set(self.partial_agents)) <= status_ids:
            raise ValueError("Unavailable agent list contains an unknown agent.")
        if set(self.evidence_supported_findings) & set(self.weakly_evidenced_findings):
            raise ValueError("A finding cannot be both evidence-supported and weakly evidenced.")
        if any(row.applied for row in self.patch_reviews) or self.approved_by_human:
            raise ValueError("AI review cannot apply or approve changes.")
        return self
