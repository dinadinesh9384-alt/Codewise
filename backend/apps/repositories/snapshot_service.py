"""Provider-independent repository snapshot storage and lifecycle services."""

import hashlib
import hmac
import re
import uuid
from datetime import timedelta
from typing import Any, Protocol

from django.db import transaction
from django.utils import timezone

from .contracts import SnapshotLifecycleResult
from .errors import (
    RepositoryGrantRequired,
    SnapshotIntegrityError,
    SnapshotNotSealed,
    SnapshotStorageError,
    WorkspaceAccessDenied,
)
from .models import ExecutionSnapshotBinding, Repository, RepositoryGrant, RepositorySnapshot, User, Workspace
from .services import create_audit_event, get_authorized_snapshot, require_workspace_membership
from .storage.contracts import SnapshotFileRecord, SnapshotStorage

_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTENT_DIGEST_PREFIX = b"codewise-snapshot-content-v1\x00"


class SnapshotManifestCodec(Protocol):
    """Provider adapter for validated manifest serialization and verification."""

    def serialize(self, manifest: Any) -> bytes: ...
    def parse(self, payload: bytes) -> Any: ...
    def verify(self, manifest: Any) -> bool: ...
    def verify_source(self, manifest: Any, *, extracted_archive, staged_archive, resolved_commit) -> bool: ...
    def file_records(self, manifest: Any) -> tuple[SnapshotFileRecord, ...]: ...


def _result(snapshot_id, status, *, manifest=None, error_code=None) -> SnapshotLifecycleResult:
    return SnapshotLifecycleResult(
        operation_id=uuid.uuid4(),
        snapshot_id=snapshot_id,
        status=status,
        manifest_digest=getattr(manifest, "manifest_sha256", None),
        file_count=getattr(manifest, "file_count", 0),
        total_bytes=getattr(manifest, "total_bytes", 0),
        error_code=error_code,
    )


def _load_snapshot(*, workspace_id, snapshot_id, lock=False) -> RepositorySnapshot:
    query = RepositorySnapshot.objects.select_related("repository", "source_grant")
    if lock:
        query = query.select_for_update()
    snapshot = query.filter(pk=snapshot_id, workspace_id=workspace_id).first()
    if snapshot is None:
        raise WorkspaceAccessDenied("Snapshot is not available in this workspace.")
    return snapshot


def _authorize_snapshot_source(*, user, snapshot: RepositorySnapshot):
    require_workspace_membership(user=user, workspace_id=snapshot.workspace_id)
    repository = snapshot.repository
    if repository.workspace_id != snapshot.workspace_id:
        raise WorkspaceAccessDenied("Snapshot is not available in this workspace.")
    if snapshot.visibility_at_capture in (Repository.Visibility.PRIVATE, Repository.Visibility.INTERNAL):
        grant = snapshot.source_grant
        if (grant is None or grant.workspace_id != snapshot.workspace_id
                or grant.repository_id != snapshot.repository_id
                or grant.status != RepositoryGrant.Status.VERIFIED_READ
                or not grant.checked_at or grant.revoked_at is not None
                or (grant.expires_at is not None and grant.expires_at <= timezone.now())
                or "contents_read" not in (grant.permissions or [])):
            raise RepositoryGrantRequired("Verified workspace repository read access is required.")


def _validate_manifest_for_snapshot(snapshot, manifest, *, archive_sha256: str | None = None) -> None:
    identity = manifest.repository
    repository = snapshot.repository
    if (identity.provider != repository.provider
            or identity.provider_repository_id != repository.provider_repository_id
            or identity.owner != repository.owner or identity.name != repository.name
            or identity.canonical_url != repository.canonical_url):
        raise SnapshotIntegrityError("Manifest repository identity does not match the snapshot.")
    if manifest.commit_sha != snapshot.commit_sha:
        raise SnapshotIntegrityError("Manifest commit does not match the snapshot.")
    if manifest.source_completeness != "unverified":
        raise SnapshotIntegrityError("Manifest source completeness must remain unverified.")
    if archive_sha256 is not None and manifest.archive_sha256 != archive_sha256:
        raise SnapshotIntegrityError("Manifest archive digest does not match the acquired source.")
    if snapshot.manifest_schema_version and snapshot.manifest_schema_version != manifest.schema_version:
        raise SnapshotIntegrityError("Manifest schema does not match the snapshot metadata.")


def _expected_refs(storage: SnapshotStorage, snapshot: RepositorySnapshot):
    receipt = storage.reference_for(snapshot.workspace_id, snapshot.id)
    return receipt


def _content_digest(files: tuple[SnapshotFileRecord, ...]) -> str:
    """Hash a versioned, unambiguous encoding of sorted manifest file records."""
    digest = hashlib.sha256(_CONTENT_DIGEST_PREFIX)
    for record in sorted(files, key=lambda item: item.path):
        path = record.path.encode("utf-8")
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(record.size_bytes.to_bytes(8, "big"))
        digest.update(bytes.fromhex(record.sha256))
    return digest.hexdigest()


def _stored_manifest(*, snapshot, storage, codec):
    refs = _expected_refs(storage, snapshot)
    if snapshot.storage_ref != refs.storage_ref or snapshot.manifest_storage_ref != refs.manifest_storage_ref:
        raise SnapshotIntegrityError("Snapshot storage references are inconsistent.")
    raw = storage.read_manifest(workspace_id=snapshot.workspace_id, snapshot_id=snapshot.id)
    manifest = codec.parse(raw)
    if not codec.verify(manifest):
        raise SnapshotIntegrityError("Stored manifest digest is invalid.")
    _validate_manifest_for_snapshot(snapshot, manifest)
    if manifest.archive_sha256 != snapshot.provenance.get("archive_sha256"):
        raise SnapshotIntegrityError("Stored manifest archive digest does not match snapshot provenance.")
    if (snapshot.manifest_digest != manifest.manifest_sha256
            or snapshot.manifest_schema_version != manifest.schema_version
            or snapshot.file_count != manifest.file_count
            or snapshot.total_bytes != manifest.total_bytes
            or snapshot.content_digest != _content_digest(codec.file_records(manifest))):
        raise SnapshotIntegrityError("Stored manifest does not match snapshot metadata.")
    if not hmac.compare_digest(codec.serialize(manifest), raw):
        raise SnapshotIntegrityError("Stored manifest is not in canonical serialized form.")
    return manifest, raw


def _advance_to_validating(snapshot: RepositorySnapshot) -> None:
    if snapshot.status == RepositorySnapshot.Status.RESOLVING:
        snapshot.status = RepositorySnapshot.Status.RETRIEVING
        snapshot.save(update_fields=("status",))
    elif snapshot.status == RepositorySnapshot.Status.FAILED:
        snapshot.status = RepositorySnapshot.Status.RETRIEVING
        snapshot.failure_category = ""
        snapshot.save(update_fields=("status", "failure_category"))
    if snapshot.status == RepositorySnapshot.Status.RETRIEVING:
        snapshot.status = RepositorySnapshot.Status.VALIDATING
        snapshot.save(update_fields=("status",))
    elif snapshot.status != RepositorySnapshot.Status.VALIDATING:
        raise SnapshotNotSealed("Snapshot is not in a writable ingestion state.")


def _record_failure(*, snapshot_id, workspace_id, actor, error_code):
    try:
        with transaction.atomic():
            snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id, lock=True)
            if snapshot.status not in (RepositorySnapshot.Status.SEALED, RepositorySnapshot.Status.DELETING,
                                       RepositorySnapshot.Status.DELETED):
                snapshot.status = RepositorySnapshot.Status.FAILED
                snapshot.failure_category = error_code
                snapshot.save(update_fields=("status", "failure_category"))
            create_audit_event(
                workspace_id=workspace_id,
                actor=actor,
                action="snapshot_storage_failed",
                outcome="failed",
                reason_code=error_code,
                snapshot_id_ref=str(snapshot_id),
            )
    except Exception:
        # The filesystem may already contain a recoverable published directory;
        # the original operation error remains authoritative.
        return


def _seal_published(*, snapshot_id, workspace_id, actor, storage, codec, manifest, raw_manifest):
    refs = _expected_refs(storage, _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id))
    with transaction.atomic():
        snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id, lock=True)
        _authorize_snapshot_source(user=actor, snapshot=snapshot)
        if snapshot.status == RepositorySnapshot.Status.SEALED:
            return snapshot
        _advance_to_validating(snapshot)
        _validate_manifest_for_snapshot(snapshot, manifest)
        verification = storage.verify_snapshot(
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            files=codec.file_records(manifest),
            manifest_bytes=raw_manifest,
        )
        if not verification.valid:
            raise SnapshotIntegrityError("Published snapshot bytes failed verification.")
        now = timezone.now()
        snapshot.status = RepositorySnapshot.Status.SEALED
        snapshot.manifest_digest = manifest.manifest_sha256
        snapshot.content_digest = _content_digest(codec.file_records(manifest))
        snapshot.manifest_storage_ref = refs.manifest_storage_ref
        snapshot.storage_ref = refs.storage_ref
        snapshot.file_count = manifest.file_count
        snapshot.total_bytes = manifest.total_bytes
        snapshot.sealed_at = now
        snapshot.provenance = {
            "archive_sha256": manifest.archive_sha256,
            "source_completeness": "unverified",
        }
        if snapshot.visibility_at_capture in (Repository.Visibility.PRIVATE, Repository.Visibility.INTERNAL):
            workspace = Workspace.objects.get(pk=workspace_id)
            snapshot.expires_at = now + timedelta(days=workspace.private_source_retention_days)
        snapshot._allow_snapshot_seal = True
        try:
            snapshot.save(update_fields=(
                "status", "manifest_digest", "content_digest", "manifest_storage_ref", "storage_ref", "file_count",
                "total_bytes", "sealed_at", "provenance", "expires_at",
            ))
        finally:
            del snapshot._allow_snapshot_seal
        create_audit_event(
            workspace_id=workspace_id, actor=actor, action="snapshot_storage_verified", outcome="succeeded",
            reason_code="integrity_verified", snapshot_id_ref=str(snapshot_id),
        )
        create_audit_event(
            workspace_id=workspace_id, actor=actor, action="snapshot_sealed", outcome="succeeded",
            reason_code="snapshot_ready", snapshot_id_ref=str(snapshot_id),
        )
        return snapshot


def store_and_seal_snapshot(
    *,
    user: User,
    workspace_id,
    snapshot_id,
    resolved_commit,
    staged_archive,
    extracted_archive,
    manifest,
    manifest_codec: SnapshotManifestCodec,
    storage: SnapshotStorage,
) -> SnapshotLifecycleResult:
    """Verify source, privately stage/publish bytes, and only then seal metadata."""
    snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id)
    _authorize_snapshot_source(user=user, snapshot=snapshot)
    try:
        _validate_manifest_for_snapshot(snapshot, manifest, archive_sha256=staged_archive.sha256)
        if manifest.commit_sha != getattr(resolved_commit, "commit_sha", None):
            raise SnapshotIntegrityError("Manifest commit does not match resolved commit metadata.")
        if (manifest.archive_sha256 != getattr(extracted_archive, "source_archive_sha256", None)
                or manifest.commit_sha != getattr(extracted_archive, "commit_sha", None)):
            raise SnapshotIntegrityError("Manifest does not match the active extracted source.")
        if not manifest_codec.verify_source(
            manifest,
            extracted_archive=extracted_archive,
            staged_archive=staged_archive,
            resolved_commit=resolved_commit,
        ):
            raise SnapshotIntegrityError("Source manifest verification failed.")
        manifest_bytes = manifest_codec.serialize(manifest)
        if not manifest_codec.verify(manifest):
            raise SnapshotIntegrityError("Manifest canonical digest verification failed.")
        records = manifest_codec.file_records(manifest)
        with transaction.atomic():
            locked = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id, lock=True)
            _authorize_snapshot_source(user=user, snapshot=locked)
            if locked.status == RepositorySnapshot.Status.SEALED:
                stored, raw = _stored_manifest(snapshot=locked, storage=storage, codec=manifest_codec)
                if stored.manifest_sha256 != manifest.manifest_sha256:
                    raise SnapshotIntegrityError("Sealed snapshot cannot be replaced by another manifest.")
                verification = storage.verify_snapshot(
                    workspace_id=workspace_id, snapshot_id=snapshot_id,
                    files=manifest_codec.file_records(stored), manifest_bytes=raw,
                )
                if not verification.valid:
                    raise SnapshotIntegrityError("Sealed snapshot storage failed integrity verification.")
                return _result(snapshot_id, "already_sealed", manifest=stored)
            _advance_to_validating(locked)
            create_audit_event(
                workspace_id=workspace_id, actor=user, action="snapshot_storage_initiated", outcome="accepted",
                reason_code="verified_source_received", snapshot_id_ref=str(snapshot_id),
            )

        receipt = storage.publish_snapshot(
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            source_root=extracted_archive.root,
            files=records,
            manifest_bytes=manifest_bytes,
        )
        verification = storage.verify_snapshot(
            workspace_id=workspace_id, snapshot_id=snapshot_id, files=records, manifest_bytes=manifest_bytes,
        )
        stored_bytes = storage.read_manifest(workspace_id=workspace_id, snapshot_id=snapshot_id)
        stored_manifest = manifest_codec.parse(stored_bytes)
        if (not verification.valid or not hmac.compare_digest(stored_bytes, manifest_bytes)
                or not manifest_codec.verify(stored_manifest)
                or stored_manifest.manifest_sha256 != manifest.manifest_sha256):
            raise SnapshotIntegrityError("Stored snapshot failed post-publication verification.")
        _seal_published(
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            actor=user,
            storage=storage,
            codec=manifest_codec,
            manifest=stored_manifest,
            raw_manifest=stored_bytes,
        )
        return _result(snapshot_id, "sealed", manifest=stored_manifest)
    except Exception as exc:
        code = getattr(exc, "code", "snapshot_storage_failed")
        if not isinstance(code, str) or not _SAFE_ERROR_CODE.fullmatch(code):
            code = "snapshot_storage_failed"
        _record_failure(snapshot_id=snapshot_id, workspace_id=workspace_id, actor=user, error_code=code)
        return _result(snapshot_id, "failed", manifest=manifest if isinstance(getattr(manifest, "manifest_sha256", None), str) else None,
                       error_code=code)


def recover_published_snapshot(
    *, user: User, workspace_id, snapshot_id, manifest_codec: SnapshotManifestCodec, storage: SnapshotStorage,
) -> SnapshotLifecycleResult:
    """Recover a verified final directory left before its database seal commit."""
    snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id)
    _authorize_snapshot_source(user=user, snapshot=snapshot)
    try:
        refs = _expected_refs(storage, snapshot)
        if snapshot.storage_ref and snapshot.storage_ref != refs.storage_ref:
            raise SnapshotIntegrityError("Snapshot storage reference does not match its opaque identity.")
        raw = storage.read_manifest(workspace_id=workspace_id, snapshot_id=snapshot_id)
        manifest = manifest_codec.parse(raw)
        if not manifest_codec.verify(manifest):
            raise SnapshotIntegrityError("Published manifest digest is invalid.")
        _validate_manifest_for_snapshot(snapshot, manifest)
        if not hmac.compare_digest(manifest_codec.serialize(manifest), raw):
            raise SnapshotIntegrityError("Published manifest is not canonical.")
        verification = storage.verify_snapshot(
            workspace_id=workspace_id, snapshot_id=snapshot_id,
            files=manifest_codec.file_records(manifest), manifest_bytes=raw,
        )
        if not verification.valid:
            raise SnapshotIntegrityError("Published snapshot failed recovery verification.")
        _seal_published(
            snapshot_id=snapshot_id, workspace_id=workspace_id, actor=user,
            storage=storage, codec=manifest_codec, manifest=manifest, raw_manifest=raw,
        )
        return _result(snapshot_id, "sealed", manifest=manifest)
    except Exception as exc:
        code = getattr(exc, "code", "snapshot_integrity_failed")
        if not isinstance(code, str) or not _SAFE_ERROR_CODE.fullmatch(code):
            code = "snapshot_integrity_failed"
        _record_failure(snapshot_id=snapshot_id, workspace_id=workspace_id, actor=user, error_code=code)
        return _result(snapshot_id, "failed", error_code=code)


def _load_authorized_sealed(*, user, workspace_id, snapshot_id):
    snapshot = get_authorized_snapshot(user=user, workspace_id=workspace_id, snapshot_id=snapshot_id)
    return snapshot


def read_snapshot_manifest(*, user, workspace_id, snapshot_id, manifest_codec, storage):
    snapshot = _load_authorized_sealed(user=user, workspace_id=workspace_id, snapshot_id=snapshot_id)
    manifest, _raw = _stored_manifest(snapshot=snapshot, storage=storage, codec=manifest_codec)
    return manifest


def list_snapshot_inventory(*, user, workspace_id, snapshot_id, manifest_codec, storage):
    manifest = read_snapshot_manifest(
        user=user, workspace_id=workspace_id, snapshot_id=snapshot_id,
        manifest_codec=manifest_codec, storage=storage,
    )
    return manifest.files


def read_snapshot_file(*, user, workspace_id, snapshot_id, relative_path, max_bytes, manifest_codec, storage) -> bytes:
    snapshot = _load_authorized_sealed(user=user, workspace_id=workspace_id, snapshot_id=snapshot_id)
    manifest, _raw = _stored_manifest(snapshot=snapshot, storage=storage, codec=manifest_codec)
    record = next((item for item in manifest.files if item.path == relative_path), None)
    if record is None:
        raise SnapshotIntegrityError("Requested repository file is not in the sealed manifest.")
    if max_bytes < record.size_bytes:
        raise ValueError("max_bytes is smaller than the requested file")
    content = storage.read_file(
        workspace_id=workspace_id, snapshot_id=snapshot_id, relative_path=relative_path,
        max_bytes=max_bytes,
    )
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != record.size_bytes or not hmac.compare_digest(digest, record.sha256):
        raise SnapshotIntegrityError("Stored repository file failed integrity verification.")
    return content


def verify_stored_snapshot(*, user, workspace_id, snapshot_id, manifest_codec, storage):
    snapshot = _load_authorized_sealed(user=user, workspace_id=workspace_id, snapshot_id=snapshot_id)
    manifest, raw = _stored_manifest(snapshot=snapshot, storage=storage, codec=manifest_codec)
    verification = storage.verify_snapshot(
        workspace_id=workspace_id, snapshot_id=snapshot_id,
        files=manifest_codec.file_records(manifest), manifest_bytes=raw,
    )
    if not verification.valid:
        raise SnapshotIntegrityError("Stored repository snapshot failed integrity verification.")
    return verification


@transaction.atomic
def record_snapshot_analysis_completed(*, user, workspace_id, snapshot_id, completed_at=None):
    snapshot = get_authorized_snapshot(user=user, workspace_id=workspace_id, snapshot_id=snapshot_id)
    snapshot = RepositorySnapshot.objects.select_for_update().get(pk=snapshot.pk)
    completed_at = completed_at or timezone.now()
    if timezone.is_naive(completed_at):
        raise ValueError("completed_at must be timezone-aware")
    snapshot.last_analysis_completed_at = completed_at
    if snapshot.visibility_at_capture in (Repository.Visibility.PRIVATE, Repository.Visibility.INTERNAL):
        workspace = Workspace.objects.get(pk=workspace_id)
        snapshot.expires_at = completed_at + timedelta(days=workspace.private_source_retention_days)
    snapshot._allow_snapshot_retention_update = True
    try:
        snapshot.save(update_fields=("last_analysis_completed_at", "expires_at"))
    finally:
        del snapshot._allow_snapshot_retention_update
    create_audit_event(
        workspace_id=workspace_id, actor=user, action="snapshot_analysis_completed", outcome="succeeded",
        reason_code="retention_refreshed", snapshot_id_ref=str(snapshot_id),
    )
    return snapshot


@transaction.atomic
def expire_due_snapshots(*, now=None) -> int:
    now = now or timezone.now()
    candidates = RepositorySnapshot.objects.filter(
        status=RepositorySnapshot.Status.SEALED,
        visibility_at_capture__in=(Repository.Visibility.PRIVATE, Repository.Visibility.INTERNAL),
        expires_at__isnull=False,
        expires_at__lte=now,
    )
    expired = 0
    for candidate in candidates:
        snapshot = RepositorySnapshot.objects.select_for_update().get(pk=candidate.pk)
        # Binding rows lack completion state. Treat every binding as potentially
        # active; this is conservative and may retain completed analyses.
        if snapshot.execution_bindings.exists():
            continue
        if snapshot.status != RepositorySnapshot.Status.SEALED or snapshot.expires_at > now:
            continue
        snapshot.status = RepositorySnapshot.Status.EXPIRED
        snapshot.save(update_fields=("status",))
        create_audit_event(
            workspace_id=snapshot.workspace_id, actor=None, action="snapshot_expired", outcome="succeeded",
            reason_code="retention_elapsed", snapshot_id_ref=str(snapshot.pk),
        )
        expired += 1
    return expired


def delete_expired_snapshot(*, workspace_id, snapshot_id, storage: SnapshotStorage, user: User | None = None) -> SnapshotLifecycleResult:
    if user is not None:
        require_workspace_membership(user=user, workspace_id=workspace_id, roles={"owner", "admin"})
    with transaction.atomic():
        snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id, lock=True)
        if snapshot.status == RepositorySnapshot.Status.DELETED:
            return _result(snapshot_id, "deleted")
        if snapshot.status not in (RepositorySnapshot.Status.EXPIRED, RepositorySnapshot.Status.DELETING):
            return _result(snapshot_id, "blocked", error_code="snapshot_not_expired")
        if snapshot.execution_bindings.exists():
            return _result(snapshot_id, "blocked", error_code="execution_binding_state_unknown")
        if snapshot.status != RepositorySnapshot.Status.DELETING:
            snapshot.status = RepositorySnapshot.Status.DELETING
            snapshot.save(update_fields=("status",))
            create_audit_event(
                workspace_id=workspace_id, actor=user, action="snapshot_deletion_initiated", outcome="accepted",
                reason_code="retention_cleanup", snapshot_id_ref=str(snapshot_id),
            )
    try:
        storage.delete_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id)
        with transaction.atomic():
            snapshot = _load_snapshot(workspace_id=workspace_id, snapshot_id=snapshot_id, lock=True)
            if snapshot.status != RepositorySnapshot.Status.DELETING:
                raise SnapshotIntegrityError("Snapshot deletion state changed unexpectedly.")
            snapshot.status = RepositorySnapshot.Status.DELETED
            snapshot.deleted_at = timezone.now()
            snapshot.save(update_fields=("status", "deleted_at"))
            create_audit_event(
                workspace_id=workspace_id, actor=user, action="snapshot_deletion_completed", outcome="succeeded",
                reason_code="content_removed", snapshot_id_ref=str(snapshot_id),
            )
        return _result(snapshot_id, "deleted")
    except Exception as exc:
        code = getattr(exc, "code", "snapshot_storage_failed")
        if not isinstance(code, str) or not _SAFE_ERROR_CODE.fullmatch(code):
            code = "snapshot_storage_failed"
        try:
            create_audit_event(
                workspace_id=workspace_id, actor=user, action="snapshot_deletion_failed", outcome="retryable",
                reason_code=code, snapshot_id_ref=str(snapshot_id),
            )
        except Exception:
            pass
        return _result(snapshot_id, "failed", error_code=code)


def cleanup_abandoned_snapshot_staging(storage: SnapshotStorage, *, older_than_seconds: int = 86_400) -> int:
    return storage.cleanup_abandoned_staging(older_than_seconds=older_than_seconds)
