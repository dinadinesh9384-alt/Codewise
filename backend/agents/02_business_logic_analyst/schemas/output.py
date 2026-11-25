from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding


class ReasoningKind(StrEnum):
    DIRECT = "direct"
    INTERPRETATION = "interpretation"
    INFERENCE = "inference"
    HYPOTHESIS = "unverified_hypothesis"


class BusinessClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    reasoning: ReasoningKind
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    uncertainty: str | None = Field(default=None, max_length=1500)


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{16}$")
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    steps: tuple[str, ...] = Field(default=(), max_length=40)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    confidence: float = Field(ge=0, le=1)


class EntityRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relationship_id: str = Field(pattern=r"^relationship_[0-9a-f]{16}$")
    source_entity: str = Field(min_length=1, max_length=240)
    target_entity: str = Field(min_length=1, max_length=240)
    relationship: str = Field(min_length=1, max_length=1000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class ExecutionPath(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path_id: str = Field(pattern=r"^path_[0-9a-f]{16}$")
    operation: str = Field(min_length=1, max_length=240)
    symbols: tuple[str, ...] = Field(default=(), max_length=40)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    completeness: str = Field(min_length=1, max_length=1000)


class BusinessAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "business-analysis.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str
    manifest_digest: str
    executive_overview: str = Field(default="", max_length=6000)
    domain_summary: str = Field(default="", max_length=6000)
    workflows: tuple[Workflow, ...] = ()
    business_rules: tuple[BusinessClaim, ...] = ()
    entities: tuple[BusinessClaim, ...] = ()
    relationships: tuple[EntityRelationship, ...] = ()
    execution_paths: tuple[ExecutionPath, ...] = ()
    shared_logic: tuple[BusinessClaim, ...] = ()
    cross_module_dependencies: tuple[BusinessClaim, ...] = ()
    potential_duplication: tuple[BusinessClaim, ...] = ()
    contradictions: tuple[BusinessClaim, ...] = ()
    uncertainties: tuple[str, ...] = ()
    coverage: str = "partial"
    limitations: tuple[str, ...] = ()
    llm_status: str = Field(pattern=r"^(completed|unavailable|failed|not_attempted)$")
    findings: tuple[Finding, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
