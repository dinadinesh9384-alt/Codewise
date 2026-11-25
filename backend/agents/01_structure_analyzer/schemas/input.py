from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalysisOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=2000, ge=1, le=20_000, strict=True)
    max_file_size_bytes: int = Field(default=256_000, ge=1, le=2_000_000, strict=True)
    max_total_content_bytes: int = Field(default=5_000_000, ge=1, le=20_000_000, strict=True)
    max_directory_depth: int = Field(default=32, ge=0, le=64, strict=True)
    detect_frameworks: bool = Field(default=True, strict=True)
    inspect_dependencies: bool = Field(default=True, strict=True)
    identify_entry_points: bool = Field(default=True, strict=True)


class StructureAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="structure-analysis.input.v1", pattern=r"^structure-analysis\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)
