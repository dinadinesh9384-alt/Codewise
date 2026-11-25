from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding, FindingCategory

PatchUpstreamAgent = Literal["structure_analyzer", "business_logic_analyst", "code_quality_analyst",
                             "security_analyst", "migration_planner"]
UpstreamStatusValue = Literal["completed", "skipped", "failed", "timed_out", "cancelled", "unavailable"]


class PatchAuthorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=40, ge=1, le=200, strict=True)
    max_file_size_bytes: int = Field(default=128_000, ge=1, le=256_000, strict=True)
    max_total_content_bytes: int = Field(default=500_000, ge=1, le=1_000_000, strict=True)
    max_prompt_bytes: int = Field(default=70_000, ge=2_000, le=150_000, strict=True)
    max_output_tokens: int = Field(default=3500, ge=128, le=8000, strict=True)
    max_proposals: int = Field(default=12, ge=1, le=30, strict=True)


class UpstreamStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: PatchUpstreamAgent
    status: UpstreamStatusValue


class PatchFinding(Finding):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: PatchUpstreamAgent
    category: FindingCategory
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def files_are_evidenced(self):
        if not set(self.related_files) <= {ref.path for ref in self.evidence if ref.path}:
            raise ValueError("Finding paths must be supported by evidence.")
        return self


class MigrationTaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^migration_task_[0-9a-f]{16}$")
    title: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    affected_files: tuple[str, ...] = Field(default=(), max_length=50)


class PatchAuthorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="patch-author.input.v1", pattern=r"^patch-author\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_statuses: tuple[UpstreamStatus, ...] = Field(min_length=5, max_length=5)
    findings: tuple[PatchFinding, ...] = Field(default=(), max_length=300)
    migration_tasks: tuple[MigrationTaskReference, ...] = Field(default=(), max_length=500)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    upstream_truncated: bool = False
    options: PatchAuthorOptions = Field(default_factory=PatchAuthorOptions)

    @model_validator(mode="after")
    def upstream_is_bound_and_unique(self):
        if len({row.agent_id for row in self.upstream_statuses}) != len(self.upstream_statuses):
            raise ValueError("Each upstream agent status must be supplied exactly once.")
        if len({row.finding_id for row in self.findings}) != len(self.findings):
            raise ValueError("Upstream finding IDs must be unique.")
        for finding in self.findings:
            if any(ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest
                   for ref in finding.evidence):
                raise ValueError("Finding evidence must belong to the requested snapshot.")
        evidence_by_id = {ref.evidence_id: ref for ref in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique.")
        if any(ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest
               for ref in self.evidence):
            raise ValueError("Evidence must belong to the requested snapshot.")
        for finding in self.findings:
            if any(evidence_by_id.get(ref.evidence_id) != ref for ref in finding.evidence):
                raise ValueError("Finding evidence must be present in the input evidence index.")
        if any(not set(task.evidence_ids) <= set(evidence_by_id) for task in self.migration_tasks):
            raise ValueError("Migration tasks must reference supplied evidence.")
        if len({row.task_id for row in self.migration_tasks}) != len(self.migration_tasks):
            raise ValueError("Migration task IDs must be unique.")
        return self
