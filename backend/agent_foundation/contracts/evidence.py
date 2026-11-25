from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceType(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    SEARCH_RESULT = "search_result"
    TOOL_OUTPUT = "tool_output"
    INFERENCE = "inference"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1, max_length=128)
    evidence_type: EvidenceType
    snapshot_id: UUID
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str | None = Field(default=None, max_length=1024)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    symbol: str | None = Field(default=None, max_length=512)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_tool_id: str | None = Field(default=None, max_length=128)
    direct: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def location_consistent(self):
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("line ranges require both start_line and end_line")
        if self.start_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.evidence_type in (EvidenceType.FILE, EvidenceType.SYMBOL) and not self.path:
            raise ValueError("file and symbol evidence require a repository path")
        return self
