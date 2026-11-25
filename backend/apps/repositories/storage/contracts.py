"""Provider-independent contracts for repository snapshot storage."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SnapshotFileRecord:
    path: str
    size_bytes: int
    sha256: str

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path or "\\" in self.path or self.path.startswith("/"):
            raise ValueError("snapshot file path must be repository-relative POSIX form")
        if unicodedata.normalize("NFC", self.path) != self.path:
            raise ValueError("snapshot file path must be NFC-normalized")
        parts = self.path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("snapshot file path contains an unsafe component")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("snapshot file size must be a non-negative integer")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("snapshot file digest must be a lowercase SHA-256 value")


@dataclass(frozen=True)
class SnapshotStorageReceipt:
    storage_ref: str
    manifest_storage_ref: str
    reused_existing: bool = False


@dataclass(frozen=True)
class SnapshotStorageVerification:
    valid: bool
    file_count: int
    total_bytes: int
    error_code: str = ""


class SnapshotStorage(Protocol):
    """Private snapshot bytes behind opaque IDs; never returns local paths."""

    def reference_for(self, workspace_id: UUID, snapshot_id: UUID) -> SnapshotStorageReceipt: ...

    def publish_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        source_root,
        files: tuple[SnapshotFileRecord, ...],
        manifest_bytes: bytes,
    ) -> SnapshotStorageReceipt: ...

    def read_manifest(self, *, workspace_id: UUID, snapshot_id: UUID) -> bytes: ...

    def read_file(self, *, workspace_id: UUID, snapshot_id: UUID, relative_path: str, max_bytes: int) -> bytes: ...

    def verify_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        files: tuple[SnapshotFileRecord, ...],
        manifest_bytes: bytes,
    ) -> SnapshotStorageVerification: ...

    def delete_snapshot(self, *, workspace_id: UUID, snapshot_id: UUID) -> None: ...

    def cleanup_abandoned_staging(self, *, older_than_seconds: int = 86_400) -> int: ...
