"""Safe, temporary extraction of staged GitHub tarballs.

This module never performs HTTP requests, follows archive links, or executes
extracted content. Only validated regular files and directories are materialized.
"""

import gzip
import hashlib
import hmac
import os
import re
import stat
import tarfile
import tempfile
import unicodedata
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from .archive import StagedArchive
from .errors import (
    GitHubArchiveExtractionError,
    GitHubArchiveExtractionLimitExceeded,
    GitHubInvalidArchive,
    GitHubUnsafeArchiveMember,
)

DEFAULT_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_MEMBERS = 20_000
DEFAULT_MAX_PATH_LENGTH = 1024
DEFAULT_MAX_PATH_DEPTH = 64
DEFAULT_MAX_PAX_BYTES_PER_MEMBER = 64 * 1024
DEFAULT_MAX_PAX_BYTES_TOTAL = 1024 * 1024
EXTRACTION_CHUNK_BYTES = 64 * 1024

_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_WINDOWS_DEVICE = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGULAR_TYPES = {tarfile.REGTYPE, tarfile.AREGTYPE}


@dataclass(frozen=True)
class ArchiveFileInventoryEntry:
    """One extracted regular file, with normalized relative path and content hash."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArchiveExtractionLimits:
    """Validated resource budget for one temporary archive extraction."""

    max_total_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_members: int = DEFAULT_MAX_MEMBERS
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH
    max_path_depth: int = DEFAULT_MAX_PATH_DEPTH
    max_archive_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES
    max_pax_bytes_per_member: int = DEFAULT_MAX_PAX_BYTES_PER_MEMBER
    max_pax_bytes_total: int = DEFAULT_MAX_PAX_BYTES_TOTAL

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_EXTRACTION_LIMITS = ArchiveExtractionLimits()


@dataclass(frozen=True)
class ExtractedArchive:
    """Temporary extraction result. `root` is valid only inside its context."""

    root: Path
    inventory: tuple[ArchiveFileInventoryEntry, ...]
    file_count: int
    total_bytes: int
    source_archive_sha256: str
    commit_sha: str
    source_completeness: Literal["unverified"] = "unverified"


@dataclass(frozen=True)
class _ValidatedMember:
    archive_path: str
    relative_path: str | None
    kind: Literal["file", "directory"]
    size_bytes: int


def _safe_text_size(value: str) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as exc:
        raise GitHubUnsafeArchiveMember("Archive metadata contains invalid Unicode.") from exc


def _member_kind(member: tarfile.TarInfo) -> Literal["file", "directory"]:
    if member.type in _REGULAR_TYPES:
        return "file"
    if member.type == tarfile.DIRTYPE:
        if member.size != 0:
            raise GitHubUnsafeArchiveMember("Archive directory entry has an invalid size.")
        return "directory"
    if member.type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
        raise GitHubUnsafeArchiveMember("Archive symbolic and hard links are not supported.")
    raise GitHubUnsafeArchiveMember("Archive contains an unsupported or special member type.")


def _normalize_member_path(
    name: str,
    kind: Literal["file", "directory"],
    *,
    max_path_length: int,
    max_path_depth: int,
) -> str:
    if not isinstance(name, str) or not name:
        raise GitHubUnsafeArchiveMember("Archive member path is empty or malformed.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        raise GitHubUnsafeArchiveMember("Archive member path contains control characters.")

    # Treat Windows separators as path separators on every platform so that a
    # backslash cannot become a harmless filename on POSIX and traversal on Windows.
    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    if normalized.startswith("/") or _DRIVE_PATH.match(normalized):
        raise GitHubUnsafeArchiveMember("Archive member path is absolute or drive-qualified.")
    if kind == "directory" and normalized.endswith("/"):
        normalized = normalized[:-1]
    elif normalized.endswith("/"):
        raise GitHubUnsafeArchiveMember("Regular file path has a directory suffix.")
    if not normalized or normalized.startswith("/") or "//" in normalized:
        raise GitHubUnsafeArchiveMember("Archive member path is empty or ambiguous.")

    components = normalized.split("/")
    if len(components) > max_path_depth:
        raise GitHubArchiveExtractionLimitExceeded("Archive path exceeds the configured depth limit.")
    if _safe_text_size(normalized) > max_path_length:
        raise GitHubArchiveExtractionLimitExceeded("Archive path exceeds the configured length limit.")
    for component in components:
        if component in {"", ".", ".."}:
            raise GitHubUnsafeArchiveMember("Archive member path contains an unsafe component.")
        if (component.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(component)
                or any(character in '<>:"|?*' for character in component)):
            raise GitHubUnsafeArchiveMember("Archive member path is not portable or is ambiguous.")
    return "/".join(components)


def _pax_size(member: tarfile.TarInfo) -> int:
    try:
        return sum(_safe_text_size(key) + _safe_text_size(value) for key, value in member.pax_headers.items())
    except (AttributeError, TypeError) as exc:
        raise GitHubUnsafeArchiveMember("Archive extended metadata is malformed.") from exc


def _preflight(
    archive_path: Path,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_members: int,
    max_path_length: int,
    max_path_depth: int,
    max_pax_bytes_per_member: int,
    max_pax_bytes_total: int,
    max_archive_uncompressed_bytes: int,
) -> tuple[list[_ValidatedMember], dict[str, Literal["file", "directory"]]]:
    descriptors: list[_ValidatedMember] = []
    original_paths: set[str] = set()
    declared_file_bytes = 0
    pax_total = 0
    try:
        # tarfile's streaming gzip reader can accept a missing gzip trailer.
        # Validate the complete gzip stream and its CRC/size before parsing tar.
        gzip_bytes = 0
        with gzip.open(archive_path, "rb") as compressed:
            while True:
                chunk = compressed.read(EXTRACTION_CHUNK_BYTES)
                if not chunk:
                    break
                gzip_bytes += len(chunk)
                if gzip_bytes > max_archive_uncompressed_bytes:
                    raise GitHubArchiveExtractionLimitExceeded("Archive exceeds the configured decompressed-size limit.")
        with archive_path.open("rb") as raw, tarfile.open(fileobj=raw, mode="r|gz") as archive:
            while True:
                member = archive.next()
                if member is None:
                    break
                if len(descriptors) >= max_members:
                    raise GitHubArchiveExtractionLimitExceeded("Archive exceeds the configured member-count limit.")
                kind = _member_kind(member)
                path = _normalize_member_path(
                    member.name,
                    kind,
                    # Apply configured limits after the provider wrapper has
                    # been removed; enforce generous hard parser bounds here.
                    max_path_length=64 * 1024,
                    max_path_depth=256,
                )
                if path in original_paths:
                    raise GitHubUnsafeArchiveMember("Archive contains duplicate member paths.")
                original_paths.add(path)
                pax_size = _pax_size(member)
                if pax_size > max_pax_bytes_per_member:
                    raise GitHubArchiveExtractionLimitExceeded("Archive member metadata exceeds its configured limit.")
                pax_total += pax_size
                if pax_total > max_pax_bytes_total:
                    raise GitHubArchiveExtractionLimitExceeded("Archive metadata exceeds its configured total limit.")
                if kind == "file":
                    if member.size < 0:
                        raise GitHubUnsafeArchiveMember("Archive file has an invalid size.")
                    if member.size > max_file_bytes:
                        raise GitHubArchiveExtractionLimitExceeded("Archive file exceeds its configured size limit.")
                    declared_file_bytes += member.size
                    if declared_file_bytes > max_total_bytes:
                        raise GitHubArchiveExtractionLimitExceeded("Archive exceeds the configured extracted-byte limit.")
                # Preserve the parser-visible name for the second pass and
                # separately retain the normalized filesystem destination.
                descriptors.append(_ValidatedMember(member.name, path, kind, member.size))
                global_pax_size = sum(_safe_text_size(key) + _safe_text_size(value)
                                      for key, value in archive.pax_headers.items())
                if global_pax_size > max_pax_bytes_total:
                    raise GitHubArchiveExtractionLimitExceeded("Archive global metadata exceeds its configured limit.")
    except (GitHubInvalidArchive, GitHubArchiveExtractionLimitExceeded):
        raise
    except (tarfile.TarError, OSError, EOFError, zlib.error, ValueError, OverflowError) as exc:
        raise GitHubInvalidArchive("Archive could not be parsed safely.") from exc

    # GitHub archive tarballs contain a generated wrapper directory. Use it only
    # as a prefix to remove; never use its untrusted name as the extraction root.
    root_candidates = [item.relative_path for item in descriptors
                       if item.kind == "directory" and item.relative_path is not None
                       and "/" not in item.relative_path]
    wrapper = None
    if len(root_candidates) == 1:
        candidate = root_candidates[0]
        if all(item.relative_path == candidate or (item.relative_path is not None
               and item.relative_path.startswith(candidate + "/")) for item in descriptors):
            wrapper = candidate

    transformed: list[_ValidatedMember] = []
    for item in descriptors:
        relative = item.relative_path or ""
        if wrapper is not None:
            if relative == wrapper:
                relative = ""
            else:
                relative = relative[len(wrapper) + 1:]
        if relative:
            if len(relative.encode("utf-8")) > max_path_length:
                raise GitHubArchiveExtractionLimitExceeded("Archive path exceeds the configured length limit.")
            if len(relative.split("/")) > max_path_depth:
                raise GitHubArchiveExtractionLimitExceeded("Archive path exceeds the configured depth limit.")
        transformed.append(_ValidatedMember(item.archive_path, relative or None, item.kind, item.size_bytes))

    # Construct a portable namespace before creating any files. This catches
    # duplicate names, case/Unicode collisions, and file/directory conflicts.
    nodes: dict[str, Literal["file", "directory"]] = {}
    casefold_paths: dict[str, str] = {}
    explicitly_seen: set[str] = set()
    for item in transformed:
        path = item.relative_path
        if path is None:
            continue
        parts = path.split("/")
        prefixes = ["/".join(parts[:index]) for index in range(1, len(parts))]
        for parent in prefixes:
            folded = parent.casefold()
            existing_case = casefold_paths.get(folded)
            if existing_case is not None and existing_case != parent:
                raise GitHubUnsafeArchiveMember("Archive paths collide under case-insensitive normalization.")
            casefold_paths[folded] = parent
            if nodes.get(parent) == "file":
                raise GitHubUnsafeArchiveMember("Archive file and directory paths conflict.")
            nodes.setdefault(parent, "directory")

        folded_path = path.casefold()
        existing_case = casefold_paths.get(folded_path)
        if existing_case is not None and existing_case != path:
            raise GitHubUnsafeArchiveMember("Archive paths collide under case-insensitive normalization.")
        casefold_paths[folded_path] = path
        if path in explicitly_seen:
            raise GitHubUnsafeArchiveMember("Archive contains duplicate member paths.")
        existing_kind = nodes.get(path)
        if existing_kind is not None and existing_kind != item.kind:
            raise GitHubUnsafeArchiveMember("Archive file and directory paths conflict.")
        if item.kind == "file" and existing_kind == "directory":
            raise GitHubUnsafeArchiveMember("Archive file would replace a directory.")
        nodes[path] = item.kind
        explicitly_seen.add(path)

    return transformed, nodes


def _ensure_directory(root: Path, relative_path: str) -> Path:
    current = root
    for component in relative_path.split("/"):
        current = current / component
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            try:
                details = current.lstat()
            except OSError as exc:
                raise GitHubArchiveExtractionError("Could not validate an extraction directory.") from exc
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise GitHubUnsafeArchiveMember("Extraction path was redirected by a filesystem object.")
        try:
            current.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise GitHubUnsafeArchiveMember("Extraction directory escaped its temporary root.") from exc
    return current


def _supports_dir_fd() -> bool:
    supported = getattr(os, "supports_dir_fd", set())
    return os.name != "nt" and os.open in supported and os.mkdir in supported


def _open_directory_chain(root: Path, relative_path: str) -> tuple[int | None, bool]:
    """Open a validated directory, using dir_fd traversal where available."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not _supports_dir_fd():
        if relative_path:
            _ensure_directory(root, relative_path)
        return None, False

    descriptor = os.open(root, flags)
    try:
        if relative_path:
            for component in relative_path.split("/"):
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
                details = os.fstat(child)
                if not stat.S_ISDIR(details.st_mode):
                    os.close(child)
                    raise GitHubUnsafeArchiveMember("Extraction path component is not a directory.")
                os.close(descriptor)
                descriptor = child
        return descriptor, True
    except BaseException:
        os.close(descriptor)
        raise


def _write_regular_file(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    root: Path,
    relative_path: str,
    *,
    declared_size: int,
    max_file_bytes: int,
    max_total_bytes: int,
    total_written: int,
) -> tuple[int, str]:
    components = relative_path.split("/")
    parent_relative = "/".join(components[:-1])
    parent = root if not parent_relative else _ensure_directory(root, parent_relative)
    destination = parent / components[-1]
    try:
        destination.relative_to(root)
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise GitHubUnsafeArchiveMember("Extraction destination escaped its temporary root.") from exc

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    parent_descriptor, use_dir_fd = _open_directory_chain(root, parent_relative)
    try:
        if use_dir_fd:
            descriptor = os.open(components[-1], flags, 0o600, dir_fd=parent_descriptor)
        else:
            descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubUnsafeArchiveMember("Extraction refused to overwrite an existing path.") from exc
    except OSError as exc:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubArchiveExtractionError("Could not create an extracted file safely.") from exc

    file_bytes = 0
    try:
        source = archive.extractfile(member)
    except BaseException:
        os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise
    if source is None:
        os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubInvalidArchive("Regular archive member has no readable content stream.")
    try:
        with os.fdopen(descriptor, "wb") as output, source:
            while True:
                chunk = source.read(EXTRACTION_CHUNK_BYTES)
                if not chunk:
                    break
                file_bytes += len(chunk)
                if file_bytes > max_file_bytes or file_bytes > declared_size:
                    raise GitHubArchiveExtractionLimitExceeded("Extracted file exceeded its declared or configured size.")
                if total_written + file_bytes > max_total_bytes:
                    raise GitHubArchiveExtractionLimitExceeded("Extraction exceeded the configured total byte limit.")
                written = output.write(chunk)
                if written != len(chunk):
                    raise GitHubArchiveExtractionError("A complete archive chunk could not be written.")
            output.flush()
    except (GitHubArchiveExtractionLimitExceeded, GitHubInvalidArchive):
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise
    except GitHubArchiveExtractionError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise
    except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubInvalidArchive("Archive member data was truncated or could not be read.") from exc
    if file_bytes != declared_size:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubInvalidArchive("Extracted file length did not match the archive header.")

    # Inventory the bytes persisted on disk, not only the input stream handed
    # to the writer. Re-open without following links and verify the file type.
    read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        if use_dir_fd:
            read_descriptor = os.open(components[-1], read_flags, dir_fd=parent_descriptor)
        else:
            read_descriptor = os.open(destination, read_flags)
        details = os.fstat(read_descriptor)
        if not stat.S_ISREG(details.st_mode):
            os.close(read_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            raise GitHubUnsafeArchiveMember("Extracted destination is not a regular file.")
    except GitHubUnsafeArchiveMember:
        raise
    except OSError as exc:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise GitHubArchiveExtractionError("Could not verify an extracted file safely.") from exc
    disk_hash = hashlib.sha256()
    disk_bytes = 0
    try:
        while True:
            chunk = os.read(read_descriptor, EXTRACTION_CHUNK_BYTES)
            if not chunk:
                break
            disk_bytes += len(chunk)
            if disk_bytes > max_file_bytes or total_written + disk_bytes > max_total_bytes:
                raise GitHubArchiveExtractionLimitExceeded("Written file exceeded an extraction byte limit.")
            disk_hash.update(chunk)
    except OSError as exc:
        raise GitHubArchiveExtractionError("Could not read back an extracted file safely.") from exc
    finally:
        os.close(read_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    if disk_bytes != file_bytes or disk_bytes != declared_size:
        raise GitHubInvalidArchive("Written file length did not match its verified archive length.")
    return disk_bytes, disk_hash.hexdigest()


def _verify_staged_archive(staged_archive: StagedArchive) -> None:
    if not isinstance(staged_archive.path, Path):
        raise GitHubInvalidArchive("Staged archive path is invalid.")
    if not isinstance(staged_archive.sha256, str) or not _SHA256.fullmatch(staged_archive.sha256):
        raise GitHubInvalidArchive("Staged archive digest is invalid.")
    if not isinstance(staged_archive.commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", staged_archive.commit_sha):
        raise GitHubInvalidArchive("Staged archive commit is invalid.")
    if isinstance(staged_archive.size_bytes, bool) or not isinstance(staged_archive.size_bytes, int) or staged_archive.size_bytes < 0:
        raise GitHubInvalidArchive("Staged archive size is invalid.")
    try:
        details = staged_archive.path.lstat()
    except OSError as exc:
        raise GitHubInvalidArchive("Staged archive is unavailable.") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise GitHubUnsafeArchiveMember("Staged archive path is not a regular file.")
    digest = hashlib.sha256()
    actual_size = 0
    try:
        with staged_archive.path.open("rb") as source:
            while True:
                chunk = source.read(EXTRACTION_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                actual_size += len(chunk)
    except OSError as exc:
        raise GitHubArchiveExtractionError("Could not verify the staged archive bytes.") from exc
    if actual_size != staged_archive.size_bytes or not hmac.compare_digest(digest.hexdigest(), staged_archive.sha256):
        raise GitHubInvalidArchive("Staged archive changed after acquisition.")


@contextmanager
def extract_staged_archive(
    staged_archive: StagedArchive,
    *,
    limits: ArchiveExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
    staging_root: str | Path | None = None,
) -> Iterator[ExtractedArchive]:
    """Safely extract a staged tarball into disposable, link-free temporary storage."""
    if not isinstance(staged_archive, StagedArchive):
        raise GitHubInvalidArchive("Extraction requires a staged GitHub archive.")
    if not isinstance(limits, ArchiveExtractionLimits):
        raise TypeError("limits must be an ArchiveExtractionLimits instance")
    _verify_staged_archive(staged_archive)
    members, nodes = _preflight(
        staged_archive.path,
        max_file_bytes=limits.max_file_bytes,
        max_total_bytes=limits.max_total_bytes,
        max_members=limits.max_members,
        max_path_length=limits.max_path_length,
        max_path_depth=limits.max_path_depth,
        max_pax_bytes_per_member=limits.max_pax_bytes_per_member,
        max_pax_bytes_total=limits.max_pax_bytes_total,
        max_archive_uncompressed_bytes=limits.max_archive_uncompressed_bytes,
    )

    base = Path(staging_root) if staging_root is not None else None
    if base is not None:
        try:
            base.lstat()
        except OSError as exc:
            raise GitHubArchiveExtractionError("Configured extraction staging root is unavailable.") from exc
        if base.is_symlink() or not base.is_dir():
            raise GitHubArchiveExtractionError("Configured extraction staging root must be a real directory.")

    try:
        with tempfile.TemporaryDirectory(
            prefix="codewise-github-extract-",
            dir=str(base) if base is not None else None,
        ) as temporary_root:
            root = Path(temporary_root).resolve(strict=True)
            if os.name != "nt":
                os.chmod(root, 0o700)
            for directory in sorted(
                (path for path, kind in nodes.items() if kind == "directory"),
                key=lambda value: (value.count("/"), value),
            ):
                _ensure_directory(root, directory)

            inventory: list[ArchiveFileInventoryEntry] = []
            total_written = 0
            try:
                with staged_archive.path.open("rb") as raw, tarfile.open(fileobj=raw, mode="r|gz") as archive:
                    index = 0
                    while True:
                        member = archive.next()
                        if member is None:
                            break
                        if index >= len(members):
                            raise GitHubInvalidArchive("Archive contents changed after validation.")
                        validated = members[index]
                        index += 1
                        if member.name != validated.archive_path:
                            raise GitHubInvalidArchive("Archive member order changed after validation.")
                        if validated.relative_path is None or validated.kind == "directory":
                            continue
                        actual_size, file_digest = _write_regular_file(
                            archive,
                            member,
                            root,
                            validated.relative_path,
                            declared_size=validated.size_bytes,
                            max_file_bytes=limits.max_file_bytes,
                            max_total_bytes=limits.max_total_bytes,
                            total_written=total_written,
                        )
                        total_written += actual_size
                        inventory.append(ArchiveFileInventoryEntry(
                            relative_path=validated.relative_path,
                            size_bytes=actual_size,
                            sha256=file_digest,
                        ))
                    if index != len(members):
                        raise GitHubInvalidArchive("Archive ended before all validated members were read.")
            except (GitHubInvalidArchive, GitHubArchiveExtractionLimitExceeded,
                    GitHubUnsafeArchiveMember, GitHubArchiveExtractionError):
                raise
            except (OSError, tarfile.TarError, EOFError, zlib.error) as exc:
                raise GitHubInvalidArchive("Archive extraction failed validation.") from exc

            # Catch changes to the staged artifact between preflight and the
            # extraction pass before exposing the temporary result.
            _verify_staged_archive(staged_archive)
            inventory.sort(key=lambda entry: entry.relative_path)
            yield ExtractedArchive(
                root=root,
                inventory=tuple(inventory),
                file_count=len(inventory),
                total_bytes=total_written,
                source_archive_sha256=staged_archive.sha256,
                commit_sha=staged_archive.commit_sha,
            )
    except (GitHubInvalidArchive, GitHubArchiveExtractionLimitExceeded,
            GitHubUnsafeArchiveMember, GitHubArchiveExtractionError):
        raise
    except OSError as exc:
        raise GitHubArchiveExtractionError("Temporary extraction failed.") from exc
