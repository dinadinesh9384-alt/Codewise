"""Sanitized, typed failures from the GitHub provider boundary."""


class GitHubError(Exception):
    code = "github_provider_error"
    retryable = False


class InvalidGitHubURL(GitHubError, ValueError):
    code = "invalid_repository_url"


class GitHubInvalidResponse(GitHubError):
    code = "github_invalid_response"


class GitHubNotFound(GitHubError):
    code = "repository_not_found"


class RepositoryNotPublic(GitHubError):
    code = "repository_not_public"


class GitHubRedirectRejected(GitHubError):
    code = "github_redirect_rejected"


class GitHubRedirectLoop(GitHubRedirectRejected):
    code = "github_redirect_loop"


class GitHubArchiveError(GitHubError):
    code = "github_archive_error"


class GitHubArchiveTooLarge(GitHubArchiveError):
    code = "github_archive_too_large"


class GitHubInvalidArchive(GitHubArchiveError):
    code = "github_invalid_archive"


class GitHubUnsafeArchiveMember(GitHubInvalidArchive):
    code = "github_unsafe_archive_member"


class GitHubArchiveExtractionLimitExceeded(GitHubArchiveTooLarge):
    code = "github_archive_extraction_limit_exceeded"


class GitHubArchiveExtractionError(GitHubArchiveError):
    code = "github_archive_extraction_failed"


class GitHubManifestIntegrityError(GitHubError):
    code = "github_manifest_integrity_failed"


class GitHubHTTPError(GitHubError):
    code = "github_http_error"

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"GitHub returned HTTP {status_code}.")


class GitHubRateLimited(GitHubError):
    code = "github_rate_limited"
    retryable = True

    def __init__(self, retry_after_seconds: float | None = None):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("GitHub rate limit reached.")


class GitHubTransportError(GitHubError):
    code = "github_transport_error"
    retryable = True


class GitHubTimeout(GitHubTransportError):
    code = "github_timeout"


class GitHubTLSFailure(GitHubTransportError):
    code = "github_tls_failure"
    retryable = False
