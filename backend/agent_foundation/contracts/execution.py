from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..permissions import Permission


class ExecutionStatus(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ValidationStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    agent_id: str
    agent_version: str
    input: Any
    repository_id: UUID
    snapshot_id: UUID
    workspace_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_capabilities: frozenset[Permission] = frozenset()
    correlation_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    mode: str = "inline"


class TimingMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    retries: int = Field(default=0, ge=0)

