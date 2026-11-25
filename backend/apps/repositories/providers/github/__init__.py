"""Anonymous GitHub provider, temporary archive, extraction, and manifest foundation.

Authorization and permanent snapshot persistence are not implemented here.
"""

from .client import GitHubClient
from .archive import StagedArchive
from .contracts import GitHubRepository, GitHubRepositoryLocator, ResolvedCommit
from .extraction import (
    ArchiveExtractionLimits,
    ArchiveFileInventoryEntry,
    ExtractedArchive,
    extract_staged_archive,
)
from .errors import (
    GitHubArchiveError,
    GitHubArchiveExtractionError,
    GitHubArchiveExtractionLimitExceeded,
    GitHubArchiveTooLarge,
    GitHubError,
    GitHubHTTPError,
    GitHubInvalidResponse,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRedirectRejected,
    GitHubRedirectLoop,
    GitHubTLSFailure,
    GitHubTimeout,
    GitHubTransportError,
    GitHubInvalidArchive,
    GitHubManifestIntegrityError,
    GitHubUnsafeArchiveMember,
    InvalidGitHubURL,
    RepositoryNotPublic,
)
from .urls import parse_repository_url, validate_full_commit_sha
from .manifest import (
    ManifestFile,
    ManifestRepositoryIdentity,
    ManifestVerificationCheck,
    ManifestVerificationResult,
    RepositoryManifest,
    build_repository_manifest,
    canonical_manifest_payload_bytes,
    compute_manifest_sha256,
    serialize_manifest,
    verify_manifest,
)
from .snapshot_adapter import GitHubManifestSnapshotAdapter

__all__ = [
    "GitHubArchiveError",
    "GitHubArchiveExtractionError",
    "GitHubArchiveExtractionLimitExceeded",
    "GitHubArchiveTooLarge",
    "ArchiveFileInventoryEntry",
    "ArchiveExtractionLimits",
    "StagedArchive",
    "GitHubClient",
    "GitHubError",
    "GitHubHTTPError",
    "GitHubInvalidResponse",
    "GitHubNotFound",
    "GitHubRateLimited",
    "GitHubRedirectRejected",
    "GitHubRedirectLoop",
    "GitHubTLSFailure",
    "GitHubRepository",
    "GitHubRepositoryLocator",
    "GitHubTimeout",
    "GitHubTransportError",
    "GitHubInvalidArchive",
    "GitHubManifestIntegrityError",
    "GitHubUnsafeArchiveMember",
    "ExtractedArchive",
    "InvalidGitHubURL",
    "RepositoryNotPublic",
    "ResolvedCommit",
    "parse_repository_url",
    "validate_full_commit_sha",
    "extract_staged_archive",
    "ManifestFile",
    "ManifestRepositoryIdentity",
    "ManifestVerificationCheck",
    "ManifestVerificationResult",
    "RepositoryManifest",
    "build_repository_manifest",
    "canonical_manifest_payload_bytes",
    "compute_manifest_sha256",
    "serialize_manifest",
    "verify_manifest",
    "GitHubManifestSnapshotAdapter",
]
