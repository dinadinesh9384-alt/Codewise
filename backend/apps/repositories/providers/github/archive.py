"""Bounded public GitHub tarball acquisition into temporary staging."""

import gzip
import hashlib
import http.client
import os
import socket
import tarfile
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal
from urllib.parse import urlsplit

from .client import GITHUB_API_HOST, GitHubClient
from .contracts import ResolvedCommit
from .errors import (
    GitHubArchiveTooLarge,
    GitHubInvalidArchive,
    GitHubRedirectLoop,
    GitHubRedirectRejected,
    GitHubTimeout,
    GitHubTransportError,
    InvalidGitHubURL,
)
from .urls import validate_full_commit_sha

ARCHIVE_HOST_ALLOWLIST = frozenset({"codeload.github.com"})
MAX_ARCHIVE_REDIRECTS = 1
ARCHIVE_CHUNK_BYTES = 64 * 1024
DEFAULT_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
ARCHIVE_CONTENT_TYPES = frozenset({"application/x-gzip", "application/gzip", "application/octet-stream"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class StagedArchive:
    """A temporary archive valid only while the staging context is open."""

    path: Path
    commit_sha: str
    sha256: str
    size_bytes: int
    content_type: str
    source_completeness: Literal["unverified"] = "unverified"


def _archive_redirect_target(location: str | None, repository, commit_sha: str) -> tuple[str, str]:
    if not location or any(ord(char) < 0x20 or ord(char) == 0x7F for char in location):
        raise GitHubRedirectRejected("GitHub archive redirect target is invalid.")
    try:
        parsed = urlsplit(location)
        port = parsed.port
    except ValueError as exc:
        raise GitHubRedirectRejected("GitHub archive redirect target is invalid.") from exc
    if (parsed.scheme.lower() != "https" or parsed.hostname is None
            or parsed.hostname.lower() not in ARCHIVE_HOST_ALLOWLIST
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.query or parsed.fragment):
        raise GitHubRedirectRejected("GitHub archive redirect target is outside the HTTPS allowlist.")
    if "%" in parsed.path or "\\" in parsed.path or "\x00" in parsed.path:
        raise GitHubRedirectRejected("GitHub archive redirect path is invalid.")
    parts = parsed.path.split("/")
    if (len(parts) != 5 or parts[0] != "" or parts[1].casefold() != repository.owner.casefold()
            or parts[2].casefold() != repository.name.casefold()
            or parts[3] not in {"legacy.tar.gz", "tar.gz"}
            or parts[4].lower() != commit_sha):
        raise GitHubRedirectRejected("GitHub archive redirect does not match the repository and commit.")
    normalized = f"https://{parsed.hostname.lower()}{parsed.path}"
    return parsed.path, normalized


def _validate_gzip_tar(path: Path, max_uncompressed_bytes: int) -> None:
    total = 0
    first_block = bytearray()
    try:
        with gzip.open(path, "rb") as archive:
            while True:
                chunk = archive.read(ARCHIVE_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_uncompressed_bytes:
                    raise GitHubArchiveTooLarge("Expanded archive exceeds its configured validation limit.")
                if len(first_block) < tarfile.BLOCKSIZE:
                    first_block.extend(chunk[:tarfile.BLOCKSIZE - len(first_block)])
    except GitHubArchiveTooLarge:
        raise
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise GitHubInvalidArchive("Downloaded archive is not a complete valid gzip stream.") from exc

    if len(first_block) < tarfile.BLOCKSIZE:
        raise GitHubInvalidArchive("Downloaded gzip stream does not contain a tar header.")
    try:
        tarfile.TarInfo.frombuf(bytes(first_block), encoding="utf-8", errors="surrogateescape")
    except (tarfile.TarError, ValueError, OverflowError) as exc:
        raise GitHubInvalidArchive("Downloaded gzip stream does not contain a valid tar header.") from exc

@contextmanager
def stage_public_archive(
    client: GitHubClient,
    resolved_commit: ResolvedCommit,
    *,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> Iterator[StagedArchive]:
    """Fetch a SHA-pinned public tarball and yield it from disposable staging."""
    if not isinstance(resolved_commit, ResolvedCommit):
        raise InvalidGitHubURL("Archive download requires a resolved commit contract.")
    commit_sha = validate_full_commit_sha(resolved_commit.commit_sha)
    repository = resolved_commit.repository
    if repository.visibility != "public":
        raise InvalidGitHubURL("Only public repositories can be downloaded by this provider.")
    if isinstance(max_download_bytes, bool) or not isinstance(max_download_bytes, int) or max_download_bytes <= 0:
        raise ValueError("max_download_bytes must be a positive integer.")
    if (isinstance(max_uncompressed_bytes, bool) or not isinstance(max_uncompressed_bytes, int)
            or max_uncompressed_bytes <= 0):
        raise ValueError("max_uncompressed_bytes must be a positive integer.")

    root = client.archive_staging_root
    if root is not None:
        try:
            root_stat = root.lstat()
        except OSError as exc:
            raise GitHubInvalidArchive("Configured archive staging root is unavailable.") from exc
        if root.is_symlink() or not root.is_dir():
            raise GitHubInvalidArchive("Configured archive staging root must be a real directory.")

    api_path = f"{repository.locator.api_path}/tarball/{commit_sha}"
    api_url = f"https://{GITHUB_API_HOST}{api_path}"
    visited = {api_url}
    redirect_count = 0
    connection = None
    try:
        connection, response = client._request_response(
            GITHUB_API_HOST,
            api_path,
            expected_statuses={302},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "CodeWiseV2-repository-provider",
            },
        )
        location = response.getheader("Location")
    finally:
        if connection is not None:
            connection.close()

    target_path, target_url = _archive_redirect_target(location, repository, commit_sha)
    redirect_count += 1
    if redirect_count > MAX_ARCHIVE_REDIRECTS:
        raise GitHubRedirectRejected("GitHub archive exceeded the redirect limit.")
    if target_url in visited:
        raise GitHubRedirectLoop("GitHub archive redirect loop detected.")
    visited.add(target_url)
    # Exactly one API -> codeload redirect is supported. We never forward cookies,
    # credentials, or API-specific headers to the content host.
    try:
        connection, response = client._request_response(
            "codeload.github.com",
            target_path,
            expected_statuses={200, *REDIRECT_STATUSES},
            headers={"Accept": "application/octet-stream", "User-Agent": "CodeWiseV2-repository-provider"},
        )
        if response.status in REDIRECT_STATUSES:
            next_location = response.getheader("Location")
            _, next_url = _archive_redirect_target(next_location, repository, commit_sha)
            if next_url in visited:
                raise GitHubRedirectLoop("GitHub archive redirect loop detected.")
            redirect_count += 1
            if redirect_count > MAX_ARCHIVE_REDIRECTS:
                raise GitHubRedirectRejected("GitHub archive exceeded the redirect limit.")
        content_type_header = response.getheader("Content-Type") or ""
        content_type = content_type_header.split(";", 1)[0].strip().lower()
        if content_type not in ARCHIVE_CONTENT_TYPES:
            raise GitHubInvalidArchive("GitHub archive response has an unexpected media type.")
        content_length_header = response.getheader("Content-Length")
        if content_length_header is not None:
            try:
                content_length = int(content_length_header)
            except ValueError as exc:
                raise GitHubInvalidArchive("GitHub archive response has an invalid Content-Length.") from exc
            if content_length < 0:
                raise GitHubInvalidArchive("GitHub archive response has an invalid Content-Length.")
            if content_length > max_download_bytes:
                raise GitHubArchiveTooLarge("GitHub archive exceeds its configured download limit.")
        else:
            content_length = None

        digest = hashlib.sha256()
        downloaded = 0
        with tempfile.TemporaryDirectory(
            prefix="codewise-github-archive-",
            dir=str(root) if root is not None else None,
        ) as staging_dir:
            archive_path = Path(staging_dir) / "repository.tar.gz"
            with archive_path.open("xb") as output:
                first_bytes = bytearray()
                while True:
                    try:
                        chunk = response.read(ARCHIVE_CHUNK_BYTES)
                    except (socket.timeout, TimeoutError) as exc:
                        raise GitHubTimeout("GitHub archive transfer timed out.") from exc
                    except (OSError, http.client.HTTPException) as exc:
                        raise GitHubTransportError("GitHub archive transfer was interrupted.") from exc
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > max_download_bytes:
                        raise GitHubArchiveTooLarge("GitHub archive exceeds its configured download limit.")
                    if len(first_bytes) < 2:
                        first_bytes.extend(chunk[:2 - len(first_bytes)])
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if content_length is not None and downloaded != content_length:
                raise GitHubTransportError("GitHub archive transfer ended before Content-Length was received.")
            if bytes(first_bytes) != b"\x1f\x8b":
                raise GitHubInvalidArchive("GitHub archive does not have a gzip signature.")
            _validate_gzip_tar(archive_path, max_uncompressed_bytes)
            yield StagedArchive(
                path=archive_path,
                commit_sha=commit_sha,
                sha256=digest.hexdigest(),
                size_bytes=downloaded,
                content_type=content_type,
            )
    finally:
        if connection is not None:
            connection.close()
