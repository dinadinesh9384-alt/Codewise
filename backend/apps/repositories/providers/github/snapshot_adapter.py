"""Adapter between the GitHub v1 manifest and provider-independent snapshots."""

from ...storage.contracts import SnapshotFileRecord
from .manifest import RepositoryManifest, serialize_manifest, verify_manifest


class GitHubManifestSnapshotAdapter:
    """Supplies the generic snapshot service with GitHub manifest operations."""

    def serialize(self, manifest: RepositoryManifest) -> bytes:
        return serialize_manifest(manifest)

    def parse(self, payload: bytes) -> RepositoryManifest:
        return RepositoryManifest.model_validate_json(payload)

    def verify(self, manifest: RepositoryManifest) -> bool:
        return verify_manifest(manifest).valid

    def verify_source(self, manifest, *, extracted_archive, staged_archive, resolved_commit) -> bool:
        return verify_manifest(
            manifest,
            extracted_archive=extracted_archive,
            staged_archive=staged_archive,
            resolved_commit=resolved_commit,
        ).valid

    def file_records(self, manifest: RepositoryManifest) -> tuple[SnapshotFileRecord, ...]:
        return tuple(
            SnapshotFileRecord(path=item.path, size_bytes=item.size_bytes, sha256=item.sha256)
            for item in manifest.files
        )
