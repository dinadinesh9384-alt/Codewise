from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CodeQualityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_files: int = Field(default=500, ge=1, le=5000, strict=True)
    max_file_size_bytes: int = Field(default=128_000, ge=1, le=512_000, strict=True)
    max_total_content_bytes: int = Field(default=2_000_000, ge=1, le=5_000_000, strict=True)
    max_prompt_bytes: int = Field(default=80_000, ge=1_000, le=200_000, strict=True)
    max_output_tokens: int = Field(default=3000, ge=128, le=8000, strict=True)
    long_function_lines: int = Field(default=80, ge=20, le=500, strict=True)
    high_cyclomatic_complexity: int = Field(default=10, ge=5, le=100, strict=True)
    deep_nesting: int = Field(default=4, ge=3, le=20, strict=True)
    large_class_lines: int = Field(default=300, ge=100, le=2000, strict=True)
    too_many_parameters: int = Field(default=8, ge=5, le=30, strict=True)
    duplicate_block_statements: int = Field(default=5, ge=3, le=20, strict=True)


class CodeQualityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_version: str = Field(default="code-quality.input.v1", pattern=r"^code-quality\.input\.v1$")
    execution_id: UUID
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    options: CodeQualityOptions = Field(default_factory=CodeQualityOptions)
