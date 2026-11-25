"""Deterministic manifests and integrity checks for temporary GitHub extracts.

This module never downloads, extracts, writes, or executes repository content.
"""

import hashlib
import hmac
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .archive import StagedArchive
from .contracts import GitHubRepository, ResolvedCommit
from .errors import GitHubManifestIntegrityError
from .extraction import (
    DEFAULT_EXTRACTION_LIMITS,
    ArchiveExtractionLimits,
    ExtractedArchive,
)

MANIFEST_SCHEMA_VERSION = "repository-manifest.v1"
CANONICALIZATION_VERSION = "codewise-canonical-json-v1"
DIGEST_ALGORITHM = "sha-256"
DIGEST_SCOPE = "canonical manifest payload excluding manifest_sha256"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_FILES = 20_000
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_WINDOWS_DEVICE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)


class ManifestContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")


class ManifestRepositoryIdentity(ManifestContract):
    provider: Literal["github"]
    provider_repository_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=39, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    canonical_url: str = Field(min_length=1, max_length=500)

    @field_validator("provider_repository_id")
    @classmethod
    def provider_id_is_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("provider repository identifier is malformed")
        return value

    @model_validator(mode="after")
    def canonical_url_matches_identity(self):
        expected = f"https://github.com/{self.owner}/{self.name}"
        if self.canonical_url != expected:
            raise ValueError("canonical_url does not match the GitHub repository identity")
        return self


class ManifestFile(ManifestContract):
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=2**63 - 1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def path_is_normalized_repository_relative(cls, value: str) -> str:
        if not value or "\x00" in value or "\\" in value or value.startswith("/") or _DRIVE_PATH.match(value):
            raise ValueError("path must be a normalized relative POSIX path")
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value or len(value.encode("utf-8")) > DEFAULT_EXTRACTION_LIMITS.max_path_length:
            raise ValueError("path is not NFC normalized or exceeds the path-length limit")
        components = value.split("/")
        if len(components) > DEFAULT_EXTRACTION_LIMITS.max_path_depth:
            raise ValueError("path exceeds the path-depth limit")
        for component in components:
            if (component in {"", ".", ".."} or component.endswith((".", " "))
                    or _WINDOWS_DEVICE.fullmatch(component)
                    or any(ord(char) < 0x20 or ord(char) == 0x7F or char in '<>:"|?*' for char in component)):
                raise ValueError("path contains an invalid or ambiguous component")
        return value


class RepositoryManifest(ManifestContract):
    schema_version: Literal["repository-manifest.v1"]
    canonicalization: Literal["codewise-canonical-json-v1"]
    repository: ManifestRepositoryIdentity
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_completeness: Literal["unverified"]
    file_count: int = Field(ge=0, le=MAX_MANIFEST_FILES)
    total_bytes: int = Field(ge=0, le=2**63 - 1)
    files: tuple[ManifestFile, ...] = Field(max_length=MAX_MANIFEST_FILES)
    digest_algorithm: Literal["sha-256"]
    digest_scope: Literal["canonical manifest payload excluding manifest_sha256"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("files")
    @classmethod
    def files_have_stable_order(cls, value: tuple[ManifestFile, ...]) -> tuple[ManifestFile, ...]:
        return tuple(sorted(value, key=lambda item: item.path))

    @model_validator(mode="after")
    def inventory_totals_and_paths_are_consistent(self):
        if self.file_count != len(self.files):
            raise ValueError("file_count does not match files")
        if self.total_bytes != sum(item.size_bytes for item in self.files):
            raise ValueError("total_bytes does not match file records")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("file paths must be unique")
        folded: dict[str, str] = {}
        node_kinds: dict[str, str] = {}
        for path in paths:
            for index, _component in enumerate(path.split("/"), 1):
                node = "/".join(path.split("/")[:index])
                key = node.casefold()
                existing = folded.get(key)
                if existing is not None and existing != node:
                    raise ValueError("paths collide under portable case-insensitive normalization")
                folded[key] = node
                kind = "file" if index == len(path.split("/")) else "directory"
                old_kind = node_kinds.get(node)
                if old_kind is not None and old_kind != kind:
                    raise ValueError("file and directory paths conflict")
                node_kinds[node] = kind
        for path in paths:
            parts = path.split("/")
            for index in range(1, len(parts)):
                if node_kinds.get("/".join(parts[:index])) == "file":
                    raise ValueError("file path has descendants")
        return self


class ManifestVerificationCheck(ManifestContract):
    name: str = Field(min_length=1, max_length=64)
    passed: bool
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=200)


class ManifestVerificationResult(ManifestContract):
    valid: bool
    checks: tuple[ManifestVerificationCheck, ...]

    @model_validator(mode="after")
    def result_matches_checks(self):
        if self.valid != all(check.passed for check in self.checks):
            raise ValueError("valid must agree with individual checks")
        return self


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _bounded_json_estimate(value: Any) -> int:
    """Bound a caller-created object before making a full JSON copy of it."""
    total = 0
    nodes = 0
    pending = [value]
    while pending:
        item = pending.pop()
        nodes += 1
        if nodes > MAX_MANIFEST_BYTES:
            raise ValueError("manifest input exceeds the configured complexity limit")
        if isinstance(item, dict):
            if len(item) > 64:
                raise ValueError("manifest input exceeds the configured object-field limit")
            total += 2 + max(0, len(item) - 1)
            if total > MAX_MANIFEST_BYTES:
                raise ValueError("manifest input exceeds the configured size limit")
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError("manifest JSON object keys must be strings")
                pending.append(child)
                pending.append(key)
                total += 1  # colon
        elif isinstance(item, (list, tuple)):
            if len(item) > MAX_MANIFEST_FILES:
                raise ValueError("manifest input exceeds the configured record-count limit")
            total += 2 + max(0, len(item) - 1)
            if total > MAX_MANIFEST_BYTES:
                raise ValueError("manifest input exceeds the configured size limit")
            pending.extend(item)
        elif isinstance(item, str):
            total += 2
            for character in item:
                ordinal = ord(character)
                total += 2 if character in {'"', "\\"} else 6 if ordinal < 0x20 else len(character.encode("utf-8"))
                if total > MAX_MANIFEST_BYTES:
                    raise ValueError("manifest input exceeds the configured size limit")
        elif item is None or isinstance(item, bool):
            total += 4 if item is None else 5
        elif isinstance(item, int):
            total += len(str(item))
        elif isinstance(item, float):
            total += 32
        else:
            raise TypeError("manifest input contains a non-JSON value")
        if total > MAX_MANIFEST_BYTES:
            raise ValueError("manifest input exceeds the configured size limit")
    return total


def canonical_manifest_payload_bytes(manifest: RepositoryManifest) -> bytes:
    """Return canonical UTF-8 JSON bytes covered by the manifest digest."""
    if not isinstance(manifest, RepositoryManifest):
        raise TypeError("manifest must be a validated RepositoryManifest")
    manifest = RepositoryManifest.model_validate(manifest.model_dump(mode="python"))
    payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    return _canonical_json_bytes(payload)


def compute_manifest_sha256(manifest: RepositoryManifest) -> str:
    return hashlib.sha256(canonical_manifest_payload_bytes(manifest)).hexdigest()


def serialize_manifest(manifest: RepositoryManifest) -> bytes:
    """Serialize the complete validated manifest in canonical UTF-8 JSON."""
    if not isinstance(manifest, RepositoryManifest):
        raise TypeError("manifest must be a validated RepositoryManifest")
    manifest = RepositoryManifest.model_validate(manifest.model_dump(mode="python"))
    if not hmac.compare_digest(manifest.manifest_sha256, compute_manifest_sha256(manifest)):
        raise GitHubManifestIntegrityError("Manifest digest does not match its canonical payload.")
    result = _canonical_json_bytes(manifest.model_dump(mode="json"))
    if len(result) > MAX_MANIFEST_BYTES:
        raise GitHubManifestIntegrityError("Serialized manifest exceeds the configured size limit.")
    return result


def build_repository_manifest(
    *,
    resolved_commit: ResolvedCommit,
    staged_archive: StagedArchive,
    extracted_archive: ExtractedArchive,
    limits: ArchiveExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
) -> RepositoryManifest:
    """Build and verify a deterministic manifest for an active extraction."""
    if not isinstance(resolved_commit, ResolvedCommit):
        raise TypeError("resolved_commit must be a ResolvedCommit")
    if not isinstance(staged_archive, StagedArchive) or not isinstance(extracted_archive, ExtractedArchive):
        raise TypeError("a staged archive and active extracted archive are required")
    repository = resolved_commit.repository
    if (resolved_commit.commit_sha != staged_archive.commit_sha
            or resolved_commit.commit_sha != extracted_archive.commit_sha
            or staged_archive.sha256 != extracted_archive.source_archive_sha256):
        raise GitHubManifestIntegrityError("Resolved source metadata does not match the staged extraction.")

    inventory = tuple(
        ManifestFile(path=item.relative_path, size_bytes=item.size_bytes, sha256=item.sha256)
        for item in extracted_archive.inventory
    )
    total_bytes = sum(item.size_bytes for item in inventory)
    if extracted_archive.file_count != len(inventory) or extracted_archive.total_bytes != total_bytes:
        raise GitHubManifestIntegrityError("Extracted inventory totals are inconsistent.")
    if extracted_archive.source_completeness != "unverified":
        raise GitHubManifestIntegrityError("Source completeness must remain unverified.")

    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "canonicalization": CANONICALIZATION_VERSION,
        "repository": {
            "provider": "github",
            "provider_repository_id": repository.provider_repository_id,
            "owner": repository.owner,
            "name": repository.name,
            "canonical_url": repository.canonical_url,
        },
        "commit_sha": resolved_commit.commit_sha,
        "archive_sha256": staged_archive.sha256,
        "source_completeness": "unverified",
        "file_count": len(inventory),
        "total_bytes": total_bytes,
        "files": tuple(item.model_dump(mode="json") for item in sorted(inventory, key=lambda item: item.path)),
        "digest_algorithm": DIGEST_ALGORITHM,
        "digest_scope": DIGEST_SCOPE,
    }
    digest = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    manifest = RepositoryManifest.model_validate({**payload, "manifest_sha256": digest})
    result = verify_manifest(
        manifest,
        extracted_archive=extracted_archive,
        staged_archive=staged_archive,
        resolved_commit=resolved_commit,
        limits=limits,
    )
    if not result.valid:
        raise GitHubManifestIntegrityError("Extracted files did not pass manifest integrity verification.")
    serialize_manifest(manifest)
    return manifest


def _check(name: str, passed: bool, error_code: str, success: str, failure: str) -> ManifestVerificationCheck:
    return ManifestVerificationCheck(
        name=name,
        passed=passed,
        error_code=None if passed else error_code,
        description=success if passed else failure,
    )


def _parse_manifest(value: RepositoryManifest | bytes | str | dict[str, Any]) -> RepositoryManifest:
    if isinstance(value, RepositoryManifest):
        return RepositoryManifest.model_validate(value.model_dump(mode="python"))
    if isinstance(value, bytes):
        if len(value) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest input exceeds the configured size limit")
        return RepositoryManifest.model_validate_json(value)
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="strict")
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest input exceeds the configured size limit")
        return RepositoryManifest.model_validate_json(raw)
    if isinstance(value, dict):
        _bounded_json_estimate(value)
        raw = _canonical_json_bytes(value)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest input exceeds the configured size limit")
        return RepositoryManifest.model_validate_json(raw)
    raise TypeError("manifest input must be a validated model, JSON bytes/string, or object")


def _scan_extracted_tree(root: Path) -> tuple[set[str], set[str], bool]:
    files: set[str] = set()
    directories: set[str] = set()
    folded: set[str] = set()
    entries_seen = 0
    invalid = False

    use_dir_fd = (
        os.name != "nt"
        and os.scandir in getattr(os, "supports_fd", set())
        and os.open in getattr(os, "supports_dir_fd", set())
    )

    def walk(directory: Path | int, prefix: str = "") -> None:
        nonlocal entries_seen, invalid
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            invalid = True
            return
        for entry in entries:
            entries_seen += 1
            if entries_seen > MAX_MANIFEST_FILES * 2:
                invalid = True
                return
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                normalized = ManifestFile(path=relative, size_bytes=0, sha256="0" * 64).path
                details = entry.stat(follow_symlinks=False)
            except (OSError, ValidationError, UnicodeError, ValueError):
                invalid = True
                continue
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if getattr(details, "st_file_attributes", 0) & reparse_flag:
                invalid = True
                continue
            folded_path = normalized.casefold()
            if folded_path in folded:
                invalid = True
                continue
            folded.add(folded_path)
            if stat.S_ISLNK(details.st_mode):
                invalid = True
            elif stat.S_ISDIR(details.st_mode):
                directories.add(normalized)
                if isinstance(directory, int):
                    child_fd = -1
                    try:
                        child_fd = os.open(
                            entry.name,
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory,
                        )
                        if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                            invalid = True
                        else:
                            walk(child_fd, normalized)
                    except OSError:
                        invalid = True
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)
                else:
                    walk(Path(entry.path), normalized)
            elif stat.S_ISREG(details.st_mode):
                files.add(normalized)
            else:
                invalid = True

    if use_dir_fd:
        root_fd = -1
        try:
            root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
            walk(root_fd)
        except OSError:
            invalid = True
        finally:
            if root_fd >= 0:
                os.close(root_fd)
    else:
        walk(root)
    return files, directories, invalid


def _read_verified_file(root: Path, relative_path: str, expected_size: int, limits: ArchiveExtractionLimits) -> tuple[int, str]:
    parts = relative_path.split("/")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    use_dir_fd = os.name != "nt" and os.open in getattr(os, "supports_dir_fd", set())
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        if use_dir_fd:
            parent_descriptor = os.open(root, root_flags)
            for component in parts[:-1]:
                child = os.open(component, root_flags, dir_fd=parent_descriptor)
                if not stat.S_ISDIR(os.fstat(child).st_mode):
                    os.close(child)
                    raise OSError("unsafe directory type")
                os.close(parent_descriptor)
                parent_descriptor = child
            descriptor = os.open(parts[-1], flags, dir_fd=parent_descriptor)
        else:
            candidate = root.joinpath(*parts)
            for index in range(1, len(parts)):
                parent = root.joinpath(*parts[:index])
                if stat.S_ISLNK(parent.lstat().st_mode) or not parent.is_dir():
                    raise OSError("unsafe directory path")
            descriptor = os.open(candidate, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            os.close(descriptor)
            descriptor = None
            raise OSError("not a regular file")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limits.max_file_bytes or size > expected_size or size > limits.max_total_bytes:
                raise OSError("file exceeded verified limits")
            digest.update(chunk)
        os.close(descriptor)
        descriptor = None
        if size != expected_size:
            raise OSError("file size mismatch")
        return size, digest.hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def verify_manifest(
    manifest_input: RepositoryManifest | bytes | str | dict[str, Any],
    *,
    extracted_archive: ExtractedArchive | None = None,
    staged_archive: StagedArchive | None = None,
    resolved_commit: ResolvedCommit | None = None,
    limits: ArchiveExtractionLimits = DEFAULT_EXTRACTION_LIMITS,
) -> ManifestVerificationResult:
    """Return structured, path-redacted checks; never modifies extracted files."""
    if not isinstance(limits, ArchiveExtractionLimits):
        check = _check("verification_limits", False, "verification_limits_invalid",
                       "Verification limits are valid.", "Verification limits are invalid.")
        return ManifestVerificationResult(valid=False, checks=(check,))
    if ((staged_archive is not None and not isinstance(staged_archive, StagedArchive))
            or (resolved_commit is not None and not isinstance(resolved_commit, ResolvedCommit))
            or (extracted_archive is not None and not isinstance(extracted_archive, ExtractedArchive))):
        check = _check("verification_context", False, "verification_context_invalid",
                       "Verification context is valid.", "Verification context is invalid.")
        return ManifestVerificationResult(valid=False, checks=(check,))
    try:
        manifest = _parse_manifest(manifest_input)
        structure_check = _check("manifest_structure", True, "manifest_structure_invalid",
                                 "Manifest structure and inventory totals are valid.", "Manifest structure is invalid.")
    except (ValidationError, ValueError, TypeError, UnicodeError, OverflowError):
        structure_check = _check("manifest_structure", False, "manifest_structure_invalid",
                                 "Manifest structure and inventory totals are valid.", "Manifest structure is invalid or exceeds input limits.")
        return ManifestVerificationResult(valid=False, checks=(structure_check,))

    checks = [structure_check]
    digest_ok = hmac.compare_digest(manifest.manifest_sha256, compute_manifest_sha256(manifest))
    checks.append(_check("manifest_digest", digest_ok, "manifest_digest_mismatch",
                         "Canonical manifest digest is valid.", "Canonical manifest digest does not match."))

    if staged_archive is not None:
        archive_ok = (manifest.archive_sha256 == staged_archive.sha256
                      and manifest.commit_sha == staged_archive.commit_sha)
        checks.append(_check("archive_linkage", archive_ok, "archive_linkage_mismatch",
                             "Manifest archive digest and commit match the staged archive.",
                             "Manifest archive digest or commit does not match the staged archive."))

    if resolved_commit is not None:
        identity = resolved_commit.repository
        repository_ok = (
            manifest.repository.provider == "github"
            and manifest.repository.provider_repository_id == identity.provider_repository_id
            and manifest.repository.owner == identity.owner
            and manifest.repository.name == identity.name
            and manifest.repository.canonical_url == identity.canonical_url
        )
        checks.append(_check("repository_identity", repository_ok, "repository_identity_mismatch",
                             "Manifest repository identity matches the resolved repository.",
                             "Manifest repository identity does not match the resolved repository."))
        commit_ok = manifest.commit_sha == resolved_commit.commit_sha
        checks.append(_check("commit_linkage", commit_ok, "commit_linkage_mismatch",
                             "Manifest commit matches the resolved commit.",
                             "Manifest commit does not match the resolved commit."))

    if extracted_archive is not None:
        archive_binding_ok = (
            manifest.archive_sha256 == extracted_archive.source_archive_sha256
            and manifest.commit_sha == extracted_archive.commit_sha
            and manifest.file_count == extracted_archive.file_count
            and manifest.total_bytes == extracted_archive.total_bytes
        )
        checks.append(_check("extraction_linkage", archive_binding_ok, "extraction_linkage_mismatch",
                             "Manifest source metadata matches the extracted archive.",
                             "Manifest source metadata does not match the extracted archive."))
        root = extracted_archive.root
        try:
            if not isinstance(root, Path):
                raise OSError("invalid extraction root")
            root_details = root.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            root_ok = (stat.S_ISDIR(root_details.st_mode) and not stat.S_ISLNK(root_details.st_mode)
                       and not getattr(root_details, "st_file_attributes", 0) & reparse_flag)
        except OSError:
            root_ok = False
        if not root_ok:
            checks.append(_check("filesystem_contents", False, "filesystem_root_invalid",
                                 "Extracted files match the manifest.", "Extracted directory is unavailable or invalid."))
        else:
            actual_files, _directories, scan_invalid = _scan_extracted_tree(root)
            expected_files = {item.path for item in manifest.files}
            sizes_and_hashes_ok = not scan_invalid and actual_files == expected_files
            actual_total_bytes = 0
            if sizes_and_hashes_ok:
                for item in manifest.files:
                    try:
                        actual_size, actual_hash = _read_verified_file(root, item.path, item.size_bytes, limits)
                    except OSError:
                        sizes_and_hashes_ok = False
                        break
                    if actual_size != item.size_bytes or not hmac.compare_digest(actual_hash, item.sha256):
                        sizes_and_hashes_ok = False
                        break
                    actual_total_bytes += actual_size
                    if actual_total_bytes > limits.max_total_bytes:
                        sizes_and_hashes_ok = False
                        break
                sizes_and_hashes_ok = sizes_and_hashes_ok and actual_total_bytes == manifest.total_bytes
            checks.append(_check("filesystem_contents", sizes_and_hashes_ok, "filesystem_content_mismatch",
                                 "Extracted files, sizes, and hashes match the manifest.",
                                 "Extracted files, paths, sizes, or hashes do not match the manifest."))

    return ManifestVerificationResult(valid=all(check.passed for check in checks), checks=tuple(checks))
