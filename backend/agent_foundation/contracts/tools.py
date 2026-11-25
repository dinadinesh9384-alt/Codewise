from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolCategory(StrEnum):
    AGENT_PRIVATE = "agent_private"
    SHARED_PLATFORM = "shared_platform"
    CONTROLLED_EXECUTION = "controlled_execution"


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_permissions: frozenset[str] = frozenset()
    category: ToolCategory
    timeout_seconds: float = Field(gt=0, le=300)
    max_output_bytes: int = Field(default=1_000_000, gt=0, le=20_000_000)
    read_only: bool = True
    sandbox_required: bool = False
    owner_agent_id: str | None = None
    version: str = "1"


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_id: str
    status: str
    output: Any = None
    error_code: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    invocation_id: str
