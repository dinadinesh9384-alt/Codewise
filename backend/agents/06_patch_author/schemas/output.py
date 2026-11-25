from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_foundation.contracts.evidence import EvidenceReference

from .input import MigrationTaskReference, PatchFinding


class PatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str = Field(pattern=r"^patch_[0-9a-f]{16}$")
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
    validation_status: Literal["validated"] = "validated"
    application_status: Literal["not_applied"] = "not_applied"


class PatchAuthorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = "patch-author.output.v1"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["awaiting_review", "abstained", "unavailable", "rejected"]
    approved: Literal[False] = False
    applied: Literal[False] = False
    llm_status: Literal["completed", "unavailable", "failed", "not_attempted"]
    validation_status: Literal["passed", "not_run", "rejected"]
    summary: str = Field(default="", max_length=5000)
    proposals: tuple[PatchProposal, ...] = Field(default=(), max_length=30)
    source_findings: tuple[PatchFinding, ...] = Field(default=(), max_length=300)
    migration_tasks: tuple[MigrationTaskReference, ...] = Field(default=(), max_length=500)
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=5000)
    missing_upstream_agents: tuple[str, ...] = Field(default=(), max_length=5)
    limitations: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()

    @model_validator(mode="after")
    def references_are_bound(self):
        evidence_by_id = {ref.evidence_id: ref for ref in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("Patch evidence IDs must be unique.")
        if any(ref.snapshot_id != self.snapshot_id or ref.manifest_digest != self.manifest_digest for ref in self.evidence):
            raise ValueError("Patch evidence must belong to the requested snapshot.")
        for finding in self.source_findings:
            if any(evidence_by_id.get(ref.evidence_id) != ref for ref in finding.evidence):
                raise ValueError("Output findings must use the snapshot-bound evidence index.")
        ids = set()
        finding_ids = {item.finding_id for item in self.source_findings}
        migration_tasks = {row.task_id: row for row in self.migration_tasks}
        if len(migration_tasks) != len(self.migration_tasks):
            raise ValueError("Migration task IDs must be unique.")
        proposal_paths = set()
        for proposal in self.proposals:
            if proposal.proposal_id in ids:
                raise ValueError("Proposal IDs must be unique.")
            ids.add(proposal.proposal_id)
            if proposal.path in proposal_paths:
                raise ValueError("Only one non-conflicting proposal may target each file.")
            proposal_paths.add(proposal.path)
            if not set(proposal.evidence_ids) <= set(evidence_by_id):
                raise ValueError("Proposal references unknown evidence.")
            if proposal.path not in {evidence_by_id[eid].path for eid in proposal.evidence_ids}:
                raise ValueError("Proposal path must be supported by its cited evidence.")
            if not set(proposal.related_finding_ids) <= finding_ids:
                raise ValueError("Proposal references unknown findings.")
            if not set(proposal.migration_task_ids) <= set(migration_tasks):
                raise ValueError("Proposal references unknown migration tasks.")
            if not proposal.related_finding_ids and not proposal.migration_task_ids:
                raise ValueError("Proposal must be justified by a finding or migration task.")
            linked_evidence = {ref.evidence_id for finding in self.source_findings
                if finding.finding_id in proposal.related_finding_ids for ref in finding.evidence}
            linked_evidence.update(evidence_id for task_id in proposal.migration_task_ids
                                   for evidence_id in migration_tasks[task_id].evidence_ids)
            if not set(proposal.evidence_ids) <= linked_evidence:
                raise ValueError("Proposal evidence must support a cited finding or migration task.")
        if self.proposals and (self.status != "awaiting_review" or self.validation_status != "passed"):
            raise ValueError("Only validated proposals may await review.")
        if self.status == "awaiting_review" and not self.proposals:
            raise ValueError("Awaiting review requires at least one validated proposal.")
        if self.approved or self.applied:
            raise ValueError("Patch Author cannot approve or apply proposals.")
        return self

    @property
    def findings(self):
        """No new Foundation findings are emitted; source findings remain provenance only."""
        return ()
