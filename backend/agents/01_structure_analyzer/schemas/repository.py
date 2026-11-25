from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from agent_foundation.contracts.evidence import EvidenceReference

from agent_foundation.contracts.evidence import EvidenceReference


class NodeType(StrEnum):
    ROOT = "root"
    DIRECTORY = "directory"
    FILE = "file"


class RepositoryNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    name: str
    node_type: NodeType
    size_bytes: int | None = Field(default=None, ge=0)
    extension: str | None = None


class LanguageStat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    language: str
    file_count: int = Field(ge=0)
    bytes: int = Field(ge=0)
    detection_methods: tuple[str, ...]
    certainty: str
    evidence: tuple[EvidenceReference, ...] = ()


class TechnologyDetection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    category: str
    confidence: str
    explanation: str
    evidence: tuple[EvidenceReference, ...]


class ConfigurationFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    configuration_type: str
    path: str
    sensitive: bool = False
    content_inspection: str
    size_bytes: int = Field(ge=0)
    evidence: EvidenceReference


class Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    constraint: str | None = None
    group: str = "default"
    direct: bool = True
    evidence: EvidenceReference | None = None


class DependencyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ecosystem: str
    manifest_path: str
    dependencies: tuple[Dependency, ...]
    parse_status: str
    evidence: EvidenceReference


class EntryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    ecosystem: str
    classification: str
    explanation: str
    evidence: tuple[EvidenceReference, ...]


class StructuralObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: str
    observation: str
    confidence: str
    evidence: tuple[EvidenceReference, ...] = ()
    supporting_paths: tuple[str, ...] = ()


class SkippedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    reason: str
    size_bytes: int | None = Field(default=None, ge=0)


class AnalysisQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    completion_status: str
    files_inspected: int = Field(ge=0)
    files_skipped: int = Field(ge=0)
    content_bytes_inspected: int = Field(default=0, ge=0)
    skipped_files: tuple[SkippedFile, ...] = ()
    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class FileDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    extension: str
    file_count: int = Field(ge=0)
    bytes: int = Field(ge=0)


class SizeDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bucket: str
    file_count: int = Field(ge=0)
    bytes: int = Field(ge=0)


class RepositoryOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    root: str = "."
    file_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    maximum_directory_depth: int = Field(ge=0)
    largest_files: tuple[dict[str, Any], ...] = ()
    file_distribution: tuple[FileDistribution, ...] = ()
    size_distribution: tuple[SizeDistribution, ...] = ()
    languages: tuple[LanguageStat, ...] = ()
    technologies: tuple[TechnologyDetection, ...] = ()
    entry_point_summary: str = ""
