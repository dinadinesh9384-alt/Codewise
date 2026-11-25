from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference


UpstreamAgent = Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst"]


class MigrationPlannerOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=600, ge=1, le=4000, strict=True)
    max_file_size_bytes: int = Field(default=256_000, ge=1, le=512_000, strict=True)
    max_total_content_bytes: int = Field(default=3_000_000, ge=1, le=5_000_000, strict=True)
    max_prompt_bytes: int = Field(default=100_000, ge=2_000, le=200_000, strict=True)
    max_output_tokens: int = Field(default=4000, ge=256, le=8000, strict=True)


class UpstreamFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: UpstreamAgent
    finding_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1200)
    severity: Literal["info", "low", "medium", "high", "critical"]
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=8)
    related_files: tuple[str, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def files_are_evidenced(self):
        evidenced = {item.path for item in self.evidence if item.path}
        if not set(self.related_files) <= evidenced:
            raise ValueError("Upstream finding files must be supported by its evidence.")
        return self


class MigrationAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="migration-planner.input.v1",
        pattern=r"^migration-planner\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_state: str | None = Field(default=None, max_length=2000)
    expected_upstream_agents: tuple[UpstreamAgent, ...] = Field(default=(), max_length=4)
    upstream_findings: tuple[UpstreamFinding, ...] = Field(default=(), max_length=200)
    upstream_findings_truncated: bool = False
    options: MigrationPlannerOptions = Field(default_factory=MigrationPlannerOptions)

    @model_validator(mode="after")
    def upstream_is_snapshot_bound(self):
        if len({item.finding_id for item in self.upstream_findings}) != len(self.upstream_findings):
            raise ValueError("Upstream finding IDs must be unique.")
        for finding in self.upstream_findings:
            if any(item.snapshot_id != self.snapshot_id or item.manifest_digest != self.manifest_digest
                   for item in finding.evidence):
                raise ValueError("Upstream evidence must belong to the requested snapshot.")
        return self
