"""Snapshot storage and sealing lifecycle tests."""

import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from .errors import SnapshotIntegrityError, SnapshotStorageError
from .models import Repository, RepositorySnapshot, User
from .providers.github import build_repository_manifest, extract_staged_archive
from .providers.github.snapshot_adapter import GitHubManifestSnapshotAdapter
from .services import create_workspace
from .snapshot_service import read_snapshot_file, recover_published_snapshot, store_and_seal_snapshot
from .storage.contracts import SnapshotFileRecord
from .storage.local import LocalSnapshotStorage
from .test_github_manifest import make_archive, repository as github_repository, resolved_commit


class LocalSnapshotStorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.source = Path(self.temp.name) / "extracted"
        self.source.mkdir()
        (self.source / "empty.txt").write_bytes(b"")
        (self.source / "src").mkdir()
        (self.source / "src" / "nested.py").write_bytes(b"print('safe')\n")
        self.storage = LocalSnapshotStorage(self.root)
        self.workspace_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.snapshot_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.records = (
            SnapshotFileRecord("empty.txt", 0, hashlib.sha256(b"").hexdigest()),
            SnapshotFileRecord("src/nested.py", len(b"print('safe')\n"), hashlib.sha256(b"print('safe')\n").hexdigest()),
        )

    def test_storage_layout_manifest_file_bytes_and_readback(self):
        payload = b'{"schema_version":"test"}'
        receipt = self.storage.publish_snapshot(
            workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
            source_root=self.source, files=self.records, manifest_bytes=payload,
        )
        target = self.root / "workspaces" / self.workspace_id / "snapshots" / self.snapshot_id
        self.assertEqual((target / "manifest.json").read_bytes(), payload)
        self.assertEqual((target / "files" / "src" / "nested.py").read_bytes(), b"print('safe')\n")
        self.assertTrue(receipt.storage_ref.startswith("local-snapshot-v1:"))
        self.assertNotIn(str(self.root), receipt.storage_ref)
        self.assertTrue(self.storage.verify_snapshot(
            workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
            files=self.records, manifest_bytes=payload,
        ).valid)
        self.assertEqual(self.storage.read_file(
            workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
            relative_path="empty.txt", max_bytes=1024,
        ), b"")
        if os.name != "nt":
            self.assertEqual((target.stat().st_mode & 0o777), 0o700)
            self.assertEqual((target / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_changed_or_missing_source_never_publishes(self):
        (self.source / "src" / "nested.py").write_bytes(b"changed")
        with self.assertRaises(SnapshotIntegrityError):
            self.storage.publish_snapshot(
                workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
                source_root=self.source, files=self.records, manifest_bytes=b"manifest",
            )
        target = self.root / "workspaces" / self.workspace_id / "snapshots" / self.snapshot_id
        self.assertFalse(target.exists())
        self.assertEqual(list((self.root / ".staging").iterdir()), [])

    def test_manifest_listed_paths_are_required_and_unexpected_files_are_rejected(self):
        payload = b"manifest"
        self.storage.publish_snapshot(
            workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
            source_root=self.source, files=self.records, manifest_bytes=payload,
        )
        stored = self.root / "workspaces" / self.workspace_id / "snapshots" / self.snapshot_id
        (stored / "files" / "extra.txt").write_bytes(b"extra")
        verification = self.storage.verify_snapshot(
            workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
            files=self.records, manifest_bytes=payload,
        )
        self.assertFalse(verification.valid)

    def test_symlink_source_is_rejected(self):
        if os.name == "nt":
            self.skipTest("the current Windows account cannot create symlinks")
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_bytes(b"outside")
        (self.source / "linked.txt").symlink_to(outside)
        record = SnapshotFileRecord("linked.txt", 6, hashlib.sha256(b"outside").hexdigest())
        with self.assertRaises(SnapshotStorageError):
            self.storage.publish_snapshot(
                workspace_id=self.workspace_id, snapshot_id=self.snapshot_id,
                source_root=self.source, files=(record,), manifest_bytes=b"manifest",
            )


class SnapshotLifecycleTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private_root = Path(self.temp.name) / "private-storage"
        self.user = User.objects.create_user(username="snapshot-owner", password="test")
        self.workspace = create_workspace(owner=self.user, name="Snapshot workspace")
        identity = github_repository()
        self.repository = Repository.objects.create(
            workspace=self.workspace, provider="github",
            provider_repository_id=identity.provider_repository_id,
            owner=identity.owner, name=identity.name,
            canonical_url=identity.canonical_url, visibility="public",
        )

    def test_verified_source_is_published_then_snapshot_is_sealed_and_readable(self):
        files = {"README.md": b"hello\n", "src/main.py": b"print('hi')\n"}
        staged = make_archive(Path(self.temp.name), files)
        extraction = extract_staged_archive(staged, staging_root=self.temp.name)
        with extraction as extracted:
            commit = resolved_commit()
            manifest = build_repository_manifest(
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
            )
            snapshot = RepositorySnapshot.objects.create(
                workspace=self.workspace, repository=self.repository,
                commit_sha=commit.commit_sha, visibility_at_capture="public",
                manifest_schema_version=manifest.schema_version,
            )
            storage = LocalSnapshotStorage(self.private_root)
            adapter = GitHubManifestSnapshotAdapter()
            result = store_and_seal_snapshot(
                user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
                manifest=manifest, manifest_codec=adapter, storage=storage,
            )
            retry = store_and_seal_snapshot(
                user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
                manifest=manifest, manifest_codec=adapter, storage=storage,
            )
        self.assertEqual(result.status, "sealed")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, RepositorySnapshot.Status.SEALED)
        self.assertEqual(snapshot.file_count, 2)
        content = read_snapshot_file(
            user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
            relative_path="README.md", max_bytes=100, manifest_codec=adapter, storage=storage,
        )
        self.assertEqual(content, b"hello\n")
        self.assertEqual(retry.status, "already_sealed")
        with self.assertRaises(ValidationError):
            RepositorySnapshot.objects.filter(pk=snapshot.pk).update(commit_sha="f" * 40)

    def test_modified_extraction_fails_without_sealing(self):
        files = {"README.md": b"hello\n"}
        staged = make_archive(Path(self.temp.name), files)
        with extract_staged_archive(staged, staging_root=self.temp.name) as extracted:
            commit = resolved_commit()
            manifest = build_repository_manifest(
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
            )
            (extracted.root / "README.md").write_bytes(b"tampered")
            snapshot = RepositorySnapshot.objects.create(
                workspace=self.workspace, repository=self.repository,
                commit_sha=commit.commit_sha, visibility_at_capture="public",
                manifest_schema_version=manifest.schema_version,
            )
            result = store_and_seal_snapshot(
                user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
                manifest=manifest, manifest_codec=GitHubManifestSnapshotAdapter(),
                storage=LocalSnapshotStorage(self.private_root),
            )
        self.assertEqual(result.status, "failed")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, RepositorySnapshot.Status.FAILED)
        self.assertFalse(snapshot.storage_ref)

    def test_published_directory_is_recovered_after_database_seal_failure(self):
        staged = make_archive(Path(self.temp.name), {"README.md": b"recover me\n"})
        with extract_staged_archive(staged, staging_root=self.temp.name) as extracted:
            commit = resolved_commit()
            manifest = build_repository_manifest(
                resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
            )
            snapshot = RepositorySnapshot.objects.create(
                workspace=self.workspace, repository=self.repository,
                commit_sha=commit.commit_sha, visibility_at_capture="public",
                manifest_schema_version=manifest.schema_version,
            )
            storage = LocalSnapshotStorage(self.private_root)
            adapter = GitHubManifestSnapshotAdapter()
            with patch("apps.repositories.snapshot_service._seal_published", side_effect=RuntimeError("simulated DB failure")):
                failed = store_and_seal_snapshot(
                    user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
                    resolved_commit=commit, staged_archive=staged, extracted_archive=extracted,
                    manifest=manifest, manifest_codec=adapter, storage=storage,
                )
            self.assertEqual(failed.status, "failed")
            snapshot.refresh_from_db()
            self.assertEqual(snapshot.status, RepositorySnapshot.Status.FAILED)
            self.assertFalse(snapshot.storage_ref)
            recovered = recover_published_snapshot(
                user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk,
                manifest_codec=adapter, storage=storage,
            )
        self.assertEqual(recovered.status, "sealed")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, RepositorySnapshot.Status.SEALED)
