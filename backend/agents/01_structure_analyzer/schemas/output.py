from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_foundation.contracts.evidence import EvidenceReference
from agent_foundation.contracts.findings import Finding
from .input import AnalysisOptions
from .repository import (AnalysisQuality, ConfigurationFile, DependencyManifest, EntryPoint,
                         RepositoryNode, RepositoryOverview, StructuralObservation, TechnologyDetection)


class StructureAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: Literal["structure-analysis.output.v1"] = "structure-analysis.output.v1"
    agent_id: Literal["structure_analyzer"] = "structure_analyzer"
    agent_version: str
    execution_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options: AnalysisOptions
    overview: RepositoryOverview
    tree: tuple[RepositoryNode, ...]
    technologies: tuple[TechnologyDetection, ...]
    configurations: tuple[ConfigurationFile, ...]
    dependency_manifests: tuple[DependencyManifest, ...]
    entry_points: tuple[EntryPoint, ...]
    observations: tuple[StructuralObservation, ...]
    quality: AnalysisQuality
    findings: tuple[Finding, ...]
    evidence: tuple[EvidenceReference, ...]

    @property
    def summary(self):
        return f"Inspected {self.quality.files_inspected} of {self.overview.file_count} files; identified {len(self.overview.languages)} language groups and {len(self.technologies)} technologies."

