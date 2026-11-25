class RepositoryDomainError(Exception):
    """Base error for repository-domain operations."""

    code = "repository_operation_failed"


class WorkspaceAccessDenied(RepositoryDomainError):
    code = "workspace_access_denied"


class RepositoryGrantRequired(RepositoryDomainError):
    code = "repository_access_required"


class SnapshotNotSealed(RepositoryDomainError):
    code = "snapshot_not_sealed"


class SnapshotWorkspaceMismatch(RepositoryDomainError):
    code = "snapshot_workspace_mismatch"


class SnapshotStorageError(RepositoryDomainError):
    code = "snapshot_storage_failed"


class SnapshotStorageConflict(SnapshotStorageError):
    code = "snapshot_storage_conflict"


class SnapshotIntegrityError(RepositoryDomainError):
    code = "snapshot_integrity_failed"


class SnapshotDeletionBlocked(RepositoryDomainError):
    code = "snapshot_deletion_blocked"


class LastWorkspaceOwnerError(RepositoryDomainError, ValueError):
    code = "last_workspace_owner"

