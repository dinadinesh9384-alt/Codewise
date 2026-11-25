from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RepositoryIdentity(StrictContract):
    provider: Literal["github"]
    provider_repository_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    canonical_url: str = Field(min_length=1, max_length=500)


class RepositoryMetadata(StrictContract):
    identity: RepositoryIdentity
    visibility: Literal["public", "private", "internal", "unknown"]
    default_branch: str | None = None
    resolved_ref: str = Field(min_length=1, max_length=255)
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    observed_at: datetime


class RepositoryAccessState(StrictContract):
    status: Literal["not_required_public", "required", "pending", "verified_read", "denied", "revoked", "expired"]
    permissions: tuple[Literal["contents_read", "contents_write", "pull_requests_write"], ...] = ()
    checked_at: datetime | None = None
    grant_ref: str | None = None

    @field_validator("permissions")
    @classmethod
    def no_duplicate_permissions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("permissions must be unique")
        return value


class SnapshotMetadata(StrictContract):
    snapshot_id: UUID
    workspace_id: UUID
    repository_id: UUID
    commit_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    visibility: Literal["public", "private", "internal", "unknown"]
    status: Literal["resolving", "retrieving", "validating", "sealed", "failed", "expired", "deleting", "deleted"]
    manifest_schema_version: str = Field(min_length=1, max_length=64)
    manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    storage_ref: str | None = Field(default=None, max_length=512)
    created_at: datetime
    sealed_at: datetime | None = None

    @field_validator("sealed_at")
    @classmethod
    def sealed_snapshot_is_complete(cls, value, info):
        if info.data.get("status") == "sealed" and value is None:
            raise ValueError("sealed snapshots require sealed_at")
        return value


class RepositoryOperationResult(StrictContract):
    operation_id: UUID
    status: Literal["accepted", "succeeded", "failed", "denied", "retryable"]
    repository_id: UUID | None = None
    snapshot_id: UUID | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    retryable: bool = False
    correlation_id: str = Field(min_length=1, max_length=128)


class SnapshotLifecycleResult(StrictContract):
    operation_id: UUID
    snapshot_id: UUID
    status: Literal["sealed", "already_sealed", "expired", "deleted", "failed", "blocked"]
    manifest_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

