from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BusinessAnalysisOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=500, ge=1, le=5000, strict=True)
    max_file_size_bytes: int = Field(default=128_000, ge=1, le=512_000, strict=True)
    max_total_content_bytes: int = Field(default=2_000_000, ge=1, le=5_000_000, strict=True)
    max_prompt_bytes: int = Field(default=80_000, ge=1_000, le=200_000, strict=True)
    max_output_tokens: int = Field(default=3000, ge=128, le=8000, strict=True)


class BusinessAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="business-analysis.input.v1", pattern=r"^business-analysis\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options: BusinessAnalysisOptions = Field(default_factory=BusinessAnalysisOptions)
