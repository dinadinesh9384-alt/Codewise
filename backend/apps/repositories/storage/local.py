"""Private local filesystem implementation of snapshot storage.

The implementation uses opaque UUID references and never serves bytes over
HTTP. Application-level checks do not constrain a privileged host operator.
"""

import hashlib
import hmac
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from django.conf import settings

from ..errors import SnapshotIntegrityError, SnapshotStorageConflict, SnapshotStorageError
from .contracts import SnapshotFileRecord, SnapshotStorageReceipt, SnapshotStorageVerification

MAX_STORED_FILES = 20_000
MAX_STORED_FILE_BYTES = 64 * 1024 * 1024
MAX_STORED_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
COPY_CHUNK_BYTES = 64 * 1024
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_DEVICE_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


class LocalSnapshotStorage:
    """Private, atomically published directory storage for development/local use."""

    def __init__(self, root: str | Path | None = None):
        configured = root if root is not None else settings.REPOSITORY_PRIVATE_STORAGE_ROOT
        configured = Path(configured).expanduser()
        if not configured.is_absolute():
            raise SnapshotStorageError("Private snapshot storage root must be absolute.")
        current = Path(configured.anchor)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        for component in configured.parts[1:]:
            current = current / component
            try:
                details = current.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(details.st_mode)
                    or getattr(details, "st_file_attributes", 0) & reparse
                    or not stat.S_ISDIR(details.st_mode)):
                raise SnapshotStorageError("Private snapshot storage root cannot traverse links or non-directories.")
        resolved = configured.resolve()
        public_roots = [Path(settings.STATIC_ROOT).resolve()]
        media_root = getattr(settings, "MEDIA_ROOT", None)
        if media_root:
            public_roots.append(Path(media_root).resolve())
        for public_root in public_roots:
            if resolved == public_root or public_root in resolved.parents or resolved in public_root.parents:
                raise SnapshotStorageError("Private snapshot storage must be separate from public asset roots.")
        self.root = resolved
        self.staging_root = self.root / ".staging"

    def reference_for(self, workspace_id: UUID, snapshot_id: UUID) -> SnapshotStorageReceipt:
        workspace = self._uuid(workspace_id)
        snapshot = self._uuid(snapshot_id)
        reference = f"local-snapshot-v1:{workspace}:{snapshot}"
        return SnapshotStorageReceipt(reference, f"{reference}:manifest")

    @staticmethod
    def _uuid(value: UUID) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SnapshotStorageError("Snapshot storage identifiers are invalid.") from exc

    def _initialize_root(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            details = self.root.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SnapshotStorageError("Private snapshot root is not a real directory.")
            if os.name != "nt":
                os.chmod(self.root, 0o700)
            self.staging_root.mkdir(mode=0o700, exist_ok=True)
            staging_details = self.staging_root.lstat()
            if stat.S_ISLNK(staging_details.st_mode) or not stat.S_ISDIR(staging_details.st_mode):
                raise SnapshotStorageError("Private staging root is not a real directory.")
            if os.name != "nt":
                os.chmod(self.staging_root, 0o700)
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Private snapshot storage is unavailable.") from exc

    def _snapshot_dir(self, workspace_id: UUID, snapshot_id: UUID) -> Path:
        workspace = self._uuid(workspace_id)
        snapshot = self._uuid(snapshot_id)
        return self.root / "workspaces" / workspace / "snapshots" / snapshot

    def _check_snapshot_path(self, workspace_id: UUID, snapshot_id: UUID, *, require_leaf: bool) -> Path:
        workspace = self._uuid(workspace_id)
        snapshot = self._uuid(snapshot_id)
        paths = (
            self.root,
            self.root / "workspaces",
            self.root / "workspaces" / workspace,
            self.root / "workspaces" / workspace / "snapshots",
            self.root / "workspaces" / workspace / "snapshots" / snapshot,
        )
        leaf = paths[-1]
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        for index, path in enumerate(paths):
            if index == len(paths) - 1 and not require_leaf and not path.exists() and not path.is_symlink():
                continue
            try:
                details = path.lstat()
            except FileNotFoundError:
                if not require_leaf:
                    continue
                raise SnapshotStorageError("Snapshot storage path is unavailable.")
            if (stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode)
                    or getattr(details, "st_file_attributes", 0) & reparse):
                raise SnapshotStorageError("Snapshot storage path is not a real directory.")
        return leaf

    @staticmethod
    def _validate_path(value: str) -> tuple[str, ...]:
        if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
                or value.startswith("/") or _DRIVE_PREFIX.match(value)
                or unicodedata.normalize("NFC", value) != value
                or len(value.encode("utf-8")) > 1024):
            raise SnapshotStorageError("Repository file path is invalid.")
        parts = tuple(value.split("/"))
        if len(parts) > 64:
            raise SnapshotStorageError("Repository file path exceeds storage limits.")
        for part in parts:
            if (part in {"", ".", ".."} or part.endswith((".", " "))
                    or _DEVICE_NAME.fullmatch(part)
                    or any(ord(char) < 0x20 or ord(char) == 0x7F or char in '<>:"|?*' for char in part)):
                raise SnapshotStorageError("Repository file path is invalid.")
        return parts

    def _validate_files(self, files: tuple[SnapshotFileRecord, ...]) -> tuple[SnapshotFileRecord, ...]:
        if not isinstance(files, tuple) or len(files) > MAX_STORED_FILES:
            raise SnapshotStorageError("Snapshot inventory exceeds storage limits.")
        paths: set[str] = set()
        folded: set[str] = set()
        nodes: dict[str, str] = {}
        total = 0
        for record in files:
            if not isinstance(record, SnapshotFileRecord):
                raise SnapshotStorageError("Snapshot inventory record is invalid.")
            parts = self._validate_path(record.path)
            if record.size_bytes > MAX_STORED_FILE_BYTES:
                raise SnapshotStorageError("Snapshot file exceeds storage limits.")
            total += record.size_bytes
            if total > MAX_STORED_TOTAL_BYTES:
                raise SnapshotStorageError("Snapshot exceeds storage limits.")
            if record.path in paths:
                raise SnapshotStorageError("Snapshot inventory contains duplicate paths.")
            paths.add(record.path)
            for index in range(1, len(parts) + 1):
                node = "/".join(parts[:index])
                casefolded = node.casefold()
                if casefolded in folded and node not in nodes:
                    raise SnapshotStorageError("Snapshot paths collide under portable normalization.")
                folded.add(casefolded)
                kind = "file" if index == len(parts) else "directory"
                if node in nodes and nodes[node] != kind:
                    raise SnapshotStorageError("Snapshot file and directory paths conflict.")
                nodes[node] = kind
        for path in paths:
            parts = path.split("/")
            if any(nodes.get("/".join(parts[:index])) == "file" for index in range(1, len(parts))):
                raise SnapshotStorageError("Snapshot file path has descendants.")
        return tuple(sorted(files, key=lambda item: item.path))

    @staticmethod
    def _ensure_real_directory(path: Path, *, mode: int = 0o700) -> None:
        try:
            path.mkdir(mode=mode, exist_ok=True)
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SnapshotStorageError("Storage path is not a real directory.")
            if os.name != "nt":
                os.chmod(path, mode)
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Could not create private snapshot directories.") from exc

    @contextmanager
    def _open_source_file(self, source_root: Path, relative_path: str):
        parts = self._validate_path(relative_path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        use_dirfd = os.name != "nt" and os.open in getattr(os, "supports_dir_fd", set())
        parent_fd = None
        descriptor = None
        try:
            root_details = source_root.lstat()
            if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
                raise SnapshotStorageError("Extracted source root is invalid.")
            if use_dirfd:
                parent_fd = os.open(source_root, directory_flags)
                for part in parts[:-1]:
                    child = os.open(part, directory_flags, dir_fd=parent_fd)
                    if not stat.S_ISDIR(os.fstat(child).st_mode):
                        os.close(child)
                        raise SnapshotStorageError("Extracted source path is invalid.")
                    os.close(parent_fd)
                    parent_fd = child
                descriptor = os.open(parts[-1], flags, dir_fd=parent_fd)
            else:
                candidate = source_root.joinpath(*parts)
                for index in range(1, len(parts)):
                    parent = source_root.joinpath(*parts[:index])
                    details = parent.lstat()
                    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                        raise SnapshotStorageError("Extracted source path is invalid.")
                descriptor = os.open(candidate, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SnapshotStorageError("Only regular extracted files can be stored.")
            yield descriptor
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Could not safely read an extracted file.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent_fd is not None:
                os.close(parent_fd)

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short file write")
            view = view[written:]

    def _copy_file(self, source_root: Path, staging_files: Path, record: SnapshotFileRecord) -> None:
        parts = self._validate_path(record.path)
        current = staging_files
        for part in parts[:-1]:
            current = current / part
            self._ensure_real_directory(current)
        destination = current / parts[-1]
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            output_fd = os.open(destination, flags, 0o600)
        except FileExistsError as exc:
            raise SnapshotStorageConflict("Snapshot storage refused to overwrite an existing file.") from exc
        except OSError as exc:
            raise SnapshotStorageError("Could not create a private snapshot file.") from exc
        file_hash = hashlib.sha256()
        total = 0
        try:
            with self._open_source_file(source_root, record.path) as source_fd:
                while True:
                    chunk = os.read(source_fd, COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_STORED_FILE_BYTES or total > record.size_bytes:
                        raise SnapshotIntegrityError("Extracted file changed or exceeded storage limits.")
                    self._write_all(output_fd, chunk)
                    file_hash.update(chunk)
            os.fsync(output_fd)
        except (SnapshotStorageError, SnapshotIntegrityError):
            raise
        except OSError as exc:
            raise SnapshotStorageError("Could not write a private snapshot file.") from exc
        finally:
            os.close(output_fd)
        if total != record.size_bytes or not hmac.compare_digest(file_hash.hexdigest(), record.sha256):
            raise SnapshotIntegrityError("Extracted file no longer matches its verified inventory.")

    def _verify_directory(
        self,
        directory: Path,
        files: tuple[SnapshotFileRecord, ...],
        manifest_bytes: bytes,
    ) -> SnapshotStorageVerification:
        try:
            root_details = directory.lstat()
            if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
                return SnapshotStorageVerification(False, 0, 0, "snapshot_directory_invalid")
            manifest_details = (directory / "manifest.json").lstat()
            if stat.S_ISLNK(manifest_details.st_mode) or not stat.S_ISREG(manifest_details.st_mode):
                return SnapshotStorageVerification(False, 0, 0, "stored_manifest_invalid")
            descriptor = os.open(
                directory / "manifest.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0),
            )
            try:
                stored_manifest = bytearray()
                while True:
                    chunk = os.read(descriptor, min(COPY_CHUNK_BYTES, MAX_MANIFEST_BYTES + 1 - len(stored_manifest)))
                    if not chunk:
                        break
                    stored_manifest.extend(chunk)
                    if len(stored_manifest) > MAX_MANIFEST_BYTES:
                        return SnapshotStorageVerification(False, 0, 0, "stored_manifest_too_large")
            finally:
                os.close(descriptor)
            stored_manifest = bytes(stored_manifest)
            if not hmac.compare_digest(stored_manifest, manifest_bytes):
                return SnapshotStorageVerification(False, 0, 0, "stored_manifest_mismatch")
            files_root = directory / "files"
            files_root_details = files_root.lstat()
            if stat.S_ISLNK(files_root_details.st_mode) or not stat.S_ISDIR(files_root_details.st_mode):
                return SnapshotStorageVerification(False, 0, 0, "stored_files_root_invalid")
            expected = {record.path: record for record in files}
            actual: dict[str, Path] = {}
            invalid = False
            seen = 0

            def walk(current: Path, prefix: str = ""):
                nonlocal invalid, seen
                try:
                    entries = sorted(os.scandir(current), key=lambda item: item.name)
                except OSError:
                    invalid = True
                    return
                for entry in entries:
                    seen += 1
                    if seen > MAX_STORED_FILES * 2:
                        invalid = True
                        return
                    relative = f"{prefix}/{entry.name}" if prefix else entry.name
                    try:
                        parts = self._validate_path(relative)
                        details = entry.stat(follow_symlinks=False)
                    except (OSError, SnapshotStorageError):
                        invalid = True
                        continue
                    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                    if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & reparse:
                        invalid = True
                    elif stat.S_ISDIR(details.st_mode):
                        walk(Path(entry.path), relative)
                    elif stat.S_ISREG(details.st_mode):
                        if relative in actual:
                            invalid = True
                        actual[relative] = Path(entry.path)
                    else:
                        invalid = True

            walk(files_root)
            if invalid or set(actual) != set(expected):
                return SnapshotStorageVerification(False, len(actual), 0, "stored_file_set_mismatch")
            total = 0
            for relative, record in expected.items():
                path = actual[relative]
                if not self._verify_stored_file(path, record):
                    return SnapshotStorageVerification(False, len(actual), total, "stored_file_digest_mismatch")
                total += record.size_bytes
            if total > MAX_STORED_TOTAL_BYTES:
                return SnapshotStorageVerification(False, len(actual), total, "stored_total_limit_exceeded")
            return SnapshotStorageVerification(True, len(actual), total)
        except OSError:
            return SnapshotStorageVerification(False, 0, 0, "snapshot_storage_unavailable")

    @staticmethod
    def _verify_stored_file(path: Path, record: SnapshotFileRecord) -> bool:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = None
        try:
            descriptor = os.open(path, flags)
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                return False
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_STORED_FILE_BYTES or total > record.size_bytes:
                    return False
                digest.update(chunk)
            return total == record.size_bytes and hmac.compare_digest(digest.hexdigest(), record.sha256)
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def publish_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        source_root: str | Path,
        files: tuple[SnapshotFileRecord, ...],
        manifest_bytes: bytes,
    ) -> SnapshotStorageReceipt:
        records = self._validate_files(files)
        if not isinstance(manifest_bytes, bytes) or not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise SnapshotStorageError("Canonical manifest is missing or exceeds storage limits.")
        self._initialize_root()
        workspace = self._uuid(workspace_id)
        snapshot = self._uuid(snapshot_id)
        target = self._check_snapshot_path(workspace_id, snapshot_id, require_leaf=False)
        receipt = self.reference_for(workspace_id, snapshot_id)
        if target.exists() or target.is_symlink():
            existing = self._verify_directory(target, records, manifest_bytes)
            if existing.valid:
                return SnapshotStorageReceipt(receipt.storage_ref, receipt.manifest_storage_ref, True)
            raise SnapshotStorageConflict("Snapshot storage already contains different or invalid data.")

        stage = Path(tempfile.mkdtemp(prefix=f"{workspace}-{snapshot}-", dir=self.staging_root))
        published = False
        try:
            if os.name != "nt":
                os.chmod(stage, 0o700)
            stage_files = stage / "files"
            self._ensure_real_directory(stage_files)
            for record in records:
                self._copy_file(Path(source_root), stage_files, record)
            manifest_path = stage / "manifest.json"
            manifest_fd = os.open(
                manifest_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                self._write_all(manifest_fd, manifest_bytes)
                os.fsync(manifest_fd)
            finally:
                os.close(manifest_fd)
            stage_verification = self._verify_directory(stage, records, manifest_bytes)
            if not stage_verification.valid:
                raise SnapshotIntegrityError("Staged snapshot failed storage verification.")
            self._ensure_real_directory(target.parent.parent.parent)
            self._ensure_real_directory(target.parent.parent)
            self._ensure_real_directory(target.parent)
            try:
                os.rename(stage, target)
                published = True
            except OSError:
                if target.exists() or target.is_symlink():
                    concurrent = self._verify_directory(target, records, manifest_bytes)
                    if concurrent.valid:
                        return SnapshotStorageReceipt(receipt.storage_ref, receipt.manifest_storage_ref, True)
                    raise SnapshotStorageConflict("Snapshot storage destination already exists.")
                raise
            final_verification = self._verify_directory(target, records, manifest_bytes)
            if not final_verification.valid:
                raise SnapshotIntegrityError("Published snapshot failed storage verification.")
            return receipt
        except (SnapshotStorageError, SnapshotIntegrityError, SnapshotStorageConflict):
            raise
        except OSError as exc:
            raise SnapshotStorageError("Snapshot storage publication failed.") from exc
        finally:
            if not published and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def read_manifest(self, *, workspace_id: UUID, snapshot_id: UUID) -> bytes:
        directory = self._check_snapshot_path(workspace_id, snapshot_id, require_leaf=True)
        try:
            details = directory.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SnapshotStorageError("Stored snapshot is unavailable.")
            path = directory / "manifest.json"
            details = path.lstat()
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & reparse
                    or not stat.S_ISREG(details.st_mode) or details.st_size > MAX_MANIFEST_BYTES):
                raise SnapshotStorageError("Stored manifest is invalid.")
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0),
            )
            try:
                content = bytearray()
                while True:
                    chunk = os.read(descriptor, min(COPY_CHUNK_BYTES, MAX_MANIFEST_BYTES + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > MAX_MANIFEST_BYTES:
                        raise SnapshotStorageError("Stored manifest exceeds storage limits.")
                return bytes(content)
            finally:
                os.close(descriptor)
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Stored manifest is unavailable.") from exc

    def read_file(self, *, workspace_id: UUID, snapshot_id: UUID, relative_path: str, max_bytes: int) -> bytes:
        parts = self._validate_path(relative_path)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_STORED_FILE_BYTES:
            raise ValueError("max_bytes must be between 1 and the configured per-file limit")
        snapshot_directory = self._check_snapshot_path(workspace_id, snapshot_id, require_leaf=True)
        directory = snapshot_directory / "files"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        try:
            for parent_path in (snapshot_directory / "files",):
                details = parent_path.lstat()
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if (stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode)
                        or getattr(details, "st_file_attributes", 0) & reparse):
                    raise SnapshotStorageError("Stored snapshot path is invalid.")
            candidate = directory.joinpath(*parts)
            for index in range(1, len(parts)):
                parent = directory.joinpath(*parts[:index])
                details = parent.lstat()
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                    raise SnapshotStorageError("Stored file path is invalid.")
            candidate_details = candidate.lstat()
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (stat.S_ISLNK(candidate_details.st_mode)
                    or getattr(candidate_details, "st_file_attributes", 0) & reparse
                    or not stat.S_ISREG(candidate_details.st_mode)
                    or candidate_details.st_size > max_bytes):
                raise SnapshotStorageError("Stored file exceeds read limits or is not a regular file.")
            descriptor = os.open(candidate, flags)
            try:
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
                    raise SnapshotStorageError("Stored file exceeds read limits or is not regular.")
                content = bytearray()
                while True:
                    chunk = os.read(descriptor, min(COPY_CHUNK_BYTES, max_bytes + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise SnapshotStorageError("Stored file exceeds read limits.")
                return bytes(content)
            finally:
                os.close(descriptor)
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Stored file is unavailable.") from exc

    def verify_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        files: tuple[SnapshotFileRecord, ...],
        manifest_bytes: bytes,
    ) -> SnapshotStorageVerification:
        try:
            records = self._validate_files(files)
        except SnapshotStorageError:
            return SnapshotStorageVerification(False, 0, 0, "storage_inventory_invalid")
        try:
            directory = self._check_snapshot_path(workspace_id, snapshot_id, require_leaf=True)
        except SnapshotStorageError:
            return SnapshotStorageVerification(False, 0, 0, "snapshot_path_invalid")
        return self._verify_directory(directory, records, manifest_bytes)

    def delete_snapshot(self, *, workspace_id: UUID, snapshot_id: UUID) -> None:
        if not self.root.exists() and not self.root.is_symlink():
            return
        target = self._check_snapshot_path(workspace_id, snapshot_id, require_leaf=False)
        try:
            if not target.exists() and not target.is_symlink():
                return
            details = target.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise SnapshotStorageError("Snapshot storage target is not a real directory.")
            shutil.rmtree(target)
        except SnapshotStorageError:
            raise
        except OSError as exc:
            raise SnapshotStorageError("Snapshot storage deletion failed.") from exc

    def cleanup_abandoned_staging(self, *, older_than_seconds: int = 86_400) -> int:
        if isinstance(older_than_seconds, bool) or not isinstance(older_than_seconds, int) or older_than_seconds < 60:
            raise ValueError("staging cleanup age must be at least 60 seconds")
        self._initialize_root()
        cutoff = time.time() - older_than_seconds
        removed = 0
        try:
            for entry in os.scandir(self.staging_root):
                try:
                    details = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(details.st_mode) and not stat.S_ISLNK(details.st_mode) and details.st_mtime < cutoff:
                        shutil.rmtree(entry.path)
                        removed += 1
                except OSError:
                    continue
        except OSError as exc:
            raise SnapshotStorageError("Could not clean abandoned staging data.") from exc
        return removed
