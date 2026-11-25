"""Small, anonymous GitHub REST client with bounded network behavior."""

import http.client
import json
import logging
import random
import socket
import ssl
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .contracts import GitHubRepository, GitHubRepositoryLocator, ResolvedCommit
from .errors import (
    GitHubError,
    GitHubHTTPError,
    GitHubInvalidResponse,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRedirectRejected,
    GitHubTLSFailure,
    GitHubTimeout,
    GitHubTransportError,
    InvalidGitHubURL,
    RepositoryNotPublic,
)
from .urls import encode_ref_path, parse_repository_url, validate_full_commit_sha

logger = logging.getLogger(__name__)

GITHUB_API_HOST = "api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ANNOTATED_TAG_DEPTH = 8


class GitHubClient:
    """GitHub public metadata/ref access; it never follows redirects or logs secrets."""

    def __init__(
        self,
        *,
        connect_timeout: float = 3.05,
        read_timeout: float = 10.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.25,
        retry_jitter_seconds: float = 0.1,
        max_retry_after_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        connection_factory: Callable[..., http.client.HTTPSConnection] = http.client.HTTPSConnection,
        archive_staging_root: str | Path | None = None,
    ):
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Connection and read timeouts must be positive.")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5.")
        if retry_base_seconds < 0 or retry_jitter_seconds < 0 or max_retry_after_seconds < 0:
            raise ValueError("Retry delays cannot be negative.")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_jitter_seconds = retry_jitter_seconds
        self.max_retry_after_seconds = max_retry_after_seconds
        self.sleep = sleep
        self.random_value = random_value
        self.connection_factory = connection_factory
        self.archive_staging_root = Path(archive_staging_root) if archive_staging_root is not None else None

    def get_public_repository(self, repository_url: str) -> GitHubRepository:
        locator = parse_repository_url(repository_url)
        payload = self._request_json(locator.api_path)
        try:
            owner = payload["owner"]["login"]
            name = payload["name"]
            provider_id = payload["id"]
            visibility = payload["visibility"]
            is_private = payload["private"]
            default_branch = payload.get("default_branch")
        except (KeyError, TypeError) as exc:
            raise GitHubInvalidResponse("GitHub repository metadata is incomplete.") from exc

        if (not isinstance(owner, str) or not isinstance(name, str)
                or owner.casefold() != locator.owner.casefold()
                or name.casefold() != locator.name.casefold()):
            raise GitHubInvalidResponse("GitHub repository identity did not match the requested repository.")
        if is_private is not False or visibility != "public":
            raise RepositoryNotPublic("The repository is not publicly readable.")
        if isinstance(provider_id, bool) or not isinstance(provider_id, (int, str)) or not str(provider_id).isdigit():
            raise GitHubInvalidResponse("GitHub repository identifier is invalid.")
        if default_branch is not None and not isinstance(default_branch, str):
            raise GitHubInvalidResponse("GitHub default branch is invalid.")
        canonical_url = f"https://github.com/{owner}/{name}"
        try:
            return GitHubRepository(
                provider_repository_id=str(provider_id),
                owner=owner,
                name=name,
                canonical_url=canonical_url,
                visibility="public",
                default_branch=default_branch,
            )
        except ValidationError as exc:
            raise GitHubInvalidResponse("GitHub repository metadata failed validation.") from exc

    def resolve_branch(self, repository: GitHubRepository, branch: str) -> ResolvedCommit:
        payload = self._request_json(
            f"{repository.locator.api_path}/git/ref/heads/{encode_ref_path(branch)}"
        )
        commit_sha = self._resolve_ref_object(payload, expected_kind="commit")
        return self._resolved(repository, branch, "branch", commit_sha)

    def resolve_tag(self, repository: GitHubRepository, tag: str) -> ResolvedCommit:
        payload = self._request_json(
            f"{repository.locator.api_path}/git/ref/tags/{encode_ref_path(tag)}"
        )
        object_type, object_sha = self._ref_object(payload)
        visited: set[str] = set()
        depth = 0
        while object_type == "tag":
            if depth >= MAX_ANNOTATED_TAG_DEPTH or object_sha in visited:
                raise GitHubInvalidResponse("Annotated tag chain is cyclic or exceeds the depth limit.")
            visited.add(object_sha)
            tag_object = self._request_json(f"{repository.locator.api_path}/git/tags/{object_sha}")
            object_type, object_sha = self._nested_tag_object(tag_object)
            depth += 1
        if object_type != "commit":
            raise GitHubInvalidResponse("Git tag does not resolve to a commit.")
        return self._resolved(repository, tag, "tag", object_sha)

    def resolve_commit(self, repository: GitHubRepository, commit_sha: str) -> ResolvedCommit:
        sha = validate_full_commit_sha(commit_sha)
        payload = self._request_json(f"{repository.locator.api_path}/commits/{sha}")
        returned_sha = payload.get("sha") if isinstance(payload, dict) else None
        try:
            resolved_sha = validate_full_commit_sha(returned_sha)
        except InvalidGitHubURL as exc:
            raise GitHubInvalidResponse("GitHub commit response did not contain a full SHA.") from exc
        if resolved_sha != sha:
            raise GitHubInvalidResponse("GitHub returned a commit different from the requested SHA.")
        return self._resolved(repository, sha, "commit", resolved_sha)

    def resolve_reference(
        self, repository: GitHubRepository, ref: str, *, kind: str
    ) -> ResolvedCommit:
        if kind == "branch":
            return self.resolve_branch(repository, ref)
        if kind == "tag":
            return self.resolve_tag(repository, ref)
        if kind == "commit":
            return self.resolve_commit(repository, ref)
        raise InvalidGitHubURL("Reference kind must be branch, tag, or commit.")

    def stage_public_archive(
        self,
        resolved_commit: ResolvedCommit,
        *,
        max_download_bytes: int = 256 * 1024 * 1024,
        max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024,
    ):
        """Return a context manager yielding one verified temporary tar.gz file."""
        from .archive import stage_public_archive

        return stage_public_archive(
            self,
            resolved_commit,
            max_download_bytes=max_download_bytes,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )

    @staticmethod
    def _resolved(repository: GitHubRepository, ref: str, kind: str, sha: str) -> ResolvedCommit:
        try:
            return ResolvedCommit(repository=repository, requested_ref=ref, ref_kind=kind, commit_sha=sha)
        except ValidationError as exc:
            raise GitHubInvalidResponse("Resolved Git reference failed validation.") from exc

    @staticmethod
    def _ref_object(payload: object) -> tuple[str, str]:
        try:
            obj = payload["object"]  # type: ignore[index]
            object_type, sha = obj["type"], obj["sha"]
        except (KeyError, TypeError) as exc:
            raise GitHubInvalidResponse("Git reference response is malformed.") from exc
        if object_type not in {"commit", "tag", "tree", "blob"}:
            raise GitHubInvalidResponse("Git reference has an unsupported object type.")
        try:
            sha = validate_full_commit_sha(sha)
        except InvalidGitHubURL as exc:
            raise GitHubInvalidResponse("Git reference did not contain a full object SHA.") from exc
        return object_type, sha

    @classmethod
    def _resolve_ref_object(cls, payload: object, *, expected_kind: str) -> str:
        object_type, sha = cls._ref_object(payload)
        if object_type != expected_kind:
            raise GitHubInvalidResponse("Git branch reference does not point directly to a commit.")
        return sha

    @classmethod
    def _nested_tag_object(cls, payload: object) -> tuple[str, str]:
        return cls._ref_object(payload)

    def _request_json(self, path: str) -> dict:
        connection = None
        try:
            connection, response = self._request_response(
                GITHUB_API_HOST,
                path,
                expected_statuses={200},
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    "User-Agent": "CodeWiseV2-repository-provider",
                },
            )
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise GitHubInvalidResponse("GitHub response exceeded the configured size limit.")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GitHubInvalidResponse("GitHub returned invalid JSON.") from exc
            if not isinstance(payload, dict):
                raise GitHubInvalidResponse("GitHub response must be a JSON object.")
            return payload
        except GitHubError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise GitHubTimeout("GitHub request timed out.") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise GitHubTransportError("GitHub network request failed.") from exc
        finally:
            if connection is not None:
                connection.close()

    def _request_response(
        self,
        host: str,
        path: str,
        *,
        expected_statuses: set[int],
        headers: dict[str, str],
    ):
        """Open an allowlisted HTTPS response, applying shared retry/status policy."""
        if host not in {GITHUB_API_HOST, "codeload.github.com"}:
            raise GitHubRedirectRejected("GitHub response host is not allowlisted.")
        if not path.startswith("/") or path.startswith("//") or "\r" in path or "\n" in path:
            raise ValueError("Provider request path must be a safe absolute API path.")
        for attempt in range(self.max_attempts):
            connection = None
            keep_open = False
            try:
                connection = self.connection_factory(
                    host,
                    timeout=self.connect_timeout,
                    context=ssl.create_default_context(),
                )
                connection.request("GET", path, headers=headers)
                if connection.sock is not None:
                    connection.sock.settimeout(self.read_timeout)
                response = connection.getresponse()
                status = response.status
                if status in expected_statuses:
                    keep_open = True
                    return connection, response
                if 300 <= status < 400:
                    raise GitHubRedirectRejected("Unexpected GitHub redirect response.")
                if status == 404:
                    raise GitHubNotFound("GitHub repository or reference was not found.")
                if status in {403, 429} and self._is_rate_limited(response):
                    retry_after = self._retry_after_seconds(response)
                    logger.warning("GitHub request was rate limited (HTTP %d).", status)
                    raise GitHubRateLimited(retry_after)
                if status < 200 or status >= 300:
                    logger.warning("GitHub request returned HTTP %d.", status)
                    raise GitHubHTTPError(status)
                raise GitHubHTTPError(status)
            except GitHubRateLimited as exc:
                if (attempt + 1 >= self.max_attempts or exc.retry_after_seconds is None
                        or exc.retry_after_seconds > self.max_retry_after_seconds):
                    raise
                self.sleep(exc.retry_after_seconds)
            except (GitHubTimeout, GitHubTransportError) as exc:
                if attempt + 1 >= self.max_attempts or not exc.retryable:
                    raise
                self.sleep(self._backoff(attempt))
            except GitHubHTTPError as exc:
                if attempt + 1 >= self.max_attempts or exc.status_code not in {500, 502, 503, 504}:
                    raise
                self.sleep(self._backoff(attempt))
            except GitHubError:
                raise
            except (socket.timeout, TimeoutError) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise GitHubTimeout("GitHub request timed out.") from exc
                self.sleep(self._backoff(attempt))
            except ssl.SSLError as exc:
                raise GitHubTLSFailure("GitHub TLS connection failed.") from exc
            except (OSError, http.client.HTTPException) as exc:
                if attempt + 1 >= self.max_attempts:
                    raise GitHubTransportError("GitHub network request failed.") from exc
                self.sleep(self._backoff(attempt))
            finally:
                if connection is not None and not keep_open:
                    connection.close()
        raise GitHubTransportError("GitHub request did not complete.")

    @staticmethod
    def _is_rate_limited(response) -> bool:
        return (
            response.getheader("Retry-After") is not None
            or response.getheader("X-RateLimit-Remaining") == "0"
        )

    @staticmethod
    def _retry_after_seconds(response) -> float | None:
        retry_after = response.getheader("Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    return None
        reset = response.getheader("X-RateLimit-Reset")
        if reset is not None:
            try:
                return max(0.0, float(reset) - time.time())
            except ValueError:
                return None
        return None

    def _backoff(self, attempt: int) -> float:
        base = self.retry_base_seconds * (2 ** attempt)
        jitter = self.retry_jitter_seconds * min(max(self.random_value(), 0.0), 1.0)
        return min(self.max_retry_after_seconds, base + jitter)
