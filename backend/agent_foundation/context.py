"""Scoped, credential-free execution context and repository reader contract."""

import re
import threading
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .errors import SnapshotUnavailableError
from .permissions import Permission, require_permissions

_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    principal_id: str
    workspace_id: UUID
    repository_id: UUID
    snapshot_id: UUID
    commit_sha: str
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: UUID
    agent_id: str
    permissions: frozenset[Permission] = frozenset()
    correlation_id: str
    trace_id: str
    deadline: datetime | None = None
    cancellation: Any = None

    @classmethod
    def make(cls, **kwargs):
        forbidden = {"github_token", "storage_credentials", "storage", "storage_path", "filesystem_path", "absolute_path"}
        if forbidden.intersection(kwargs):
            raise ValueError("Execution context cannot contain credentials or filesystem access.")
        return cls(**kwargs)

    def check_active(self):
        if self.cancellation is not None and self.cancellation.is_set():
            from .errors import ExecutionCancelledError
            raise ExecutionCancelledError("Execution was cancelled.")
        if self.deadline and datetime.now(timezone.utc) >= self.deadline:
            from .errors import AgentTimeoutError
            raise AgentTimeoutError("Execution deadline elapsed.")


class RepositoryReader(Protocol):
    def read_file(self, context: ExecutionContext, path: str, *, max_bytes: int = 1_000_000) -> bytes: ...
    def list_paths(self, context: ExecutionContext) -> tuple[str, ...]: ...
    def file_metadata(self, context: ExecutionContext, path: str) -> dict: ...


def validate_relative_path(path: str) -> str:
    if (not isinstance(path, str) or not path or "\\" in path or path.startswith("/")
            or re.match(r"^[A-Za-z]:", path) or "\x00" in path):
        raise ValueError("path must be a repository-relative POSIX path")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("path contains an unsafe component")
    return path


class SnapshotRepositoryReader:
    """Authorized adapter to repository snapshot services; storage details stay server-side."""
    def __init__(self, *, user, manifest_codec, storage, max_file_bytes=1_000_000, max_response_bytes=5_000_000):
        self._user = user
        self._codec = manifest_codec
        self._storage = storage
        self._max_file_bytes = max_file_bytes
        self._max_response_bytes = max_response_bytes

    def _authorized(self, context):
        from apps.repositories.services import get_authorized_snapshot
        snapshot = get_authorized_snapshot(user=self._user, workspace_id=context.workspace_id, snapshot_id=context.snapshot_id)
        if snapshot.repository_id != context.repository_id or snapshot.commit_sha != context.commit_sha:
            raise SnapshotUnavailableError("Snapshot binding does not match this execution.")
        if snapshot.manifest_digest != context.manifest_digest:
            raise SnapshotUnavailableError("Snapshot manifest does not match this execution.")
        return snapshot

    def _manifest(self, context):
        from apps.repositories.snapshot_service import read_snapshot_manifest
        self._authorized(context)
        return read_snapshot_manifest(user=self._user, workspace_id=context.workspace_id,
            snapshot_id=context.snapshot_id, manifest_codec=self._codec, storage=self._storage)

    def read_file(self, context, path, *, max_bytes=1_000_000):
        from apps.repositories.snapshot_service import read_snapshot_file
        validate_relative_path(path)
        require_permissions(context.permissions, {Permission.REPOSITORY_READ})
        bound = min(max_bytes, self._max_file_bytes, self._max_response_bytes)
        if bound <= 0:
            raise ValueError("max_bytes must be positive")
        self._authorized(context)
        return read_snapshot_file(user=self._user, workspace_id=context.workspace_id, snapshot_id=context.snapshot_id,
            relative_path=path, max_bytes=bound, manifest_codec=self._codec, storage=self._storage)

    def list_paths(self, context):
        return tuple(item["path"] for item in self.list_file_metadata(context))

    def list_file_metadata(self, context):
        """Return integrity-verified manifest metadata in one authorized manifest read."""
        require_permissions(context.permissions, {Permission.REPOSITORY_READ})
        manifest = self._manifest(context)
        return tuple({"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
                     for item in manifest.files)

    def file_metadata(self, context, path):
        from apps.repositories.snapshot_service import read_snapshot_file
        validate_relative_path(path)
        require_permissions(context.permissions, {Permission.REPOSITORY_READ})
        manifest = self._manifest(context)
        record = next((item for item in manifest.files if item.path == path), None)
        if record is None:
            raise SnapshotUnavailableError("File is not present in the sealed snapshot.")
        result = {"path": record.path, "size_bytes": record.size_bytes, "sha256": record.sha256}
        if record.size_bytes <= self._max_file_bytes:
            content = read_snapshot_file(user=self._user, workspace_id=context.workspace_id,
                snapshot_id=context.snapshot_id, relative_path=path, max_bytes=self._max_file_bytes,
                manifest_codec=self._codec, storage=self._storage)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                result["line_count"] = len(text.splitlines())
        return result

    def search(self, context, query: str, *, max_matches=100):
        require_permissions(context.permissions, {Permission.REPOSITORY_SEARCH, Permission.REPOSITORY_READ})
        if not query or len(query) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        matches = []
        for path in self.list_paths(context):
            if len(matches) >= max_matches:
                break
            data = self.read_file(context, path, max_bytes=min(self._max_file_bytes, self._max_response_bytes))
            for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": path, "line": number, "text": line[:500]})
                    if len(matches) >= max_matches:
                        break
        return tuple(matches)
