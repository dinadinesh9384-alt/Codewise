"""Contract, determinism, and filesystem-integrity tests for GitHub manifests."""

import hashlib
import copy
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from apps.repositories.providers.github import (
    GitHubManifestIntegrityError,
    GitHubRepository,
    ManifestFile,
    RepositoryManifest,
    ResolvedCommit,
    StagedArchive,
    build_repository_manifest,
    canonical_manifest_payload_bytes,
    compute_manifest_sha256,
    serialize_manifest,
    verify_manifest,
)
from apps.repositories.providers.github.manifest import MAX_MANIFEST_BYTES

COMMIT = "a" * 40


def make_archive(parent: Path, files: dict[str, bytes]) -> StagedArchive:
    output = io.BytesIO()
    root = "archive-generated-wrapper"
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        directory = tarfile.TarInfo(root + "/")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for relative_path, content in files.items():
            info = tarfile.TarInfo(f"{root}/{relative_path}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    body = output.getvalue()
    path = parent / "source.tar.gz"
    path.write_bytes(body)
    return StagedArchive(
        path=path,
        commit_sha=COMMIT,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        content_type="application/x-gzip",
    )


def repository() -> GitHubRepository:
    return GitHubRepository(
        provider_repository_id="12345",
        owner="octo-org",
        name="demo",
        canonical_url="https://github.com/octo-org/demo",
        visibility="public",
        default_branch="main",
    )


def resolved_commit(commit_sha: str = COMMIT, repo: GitHubRepository | None = None) -> ResolvedCommit:
    return ResolvedCommit(
        repository=repo or repository(),
        requested_ref="main",
        ref_kind="branch",
        commit_sha=commit_sha,
    )


class GitHubManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.files = {"src/main.py": b"print('hello')\n", "README.md": b"hello\n"}
        self.staged = make_archive(self.parent, self.files)
        self.extraction_context = __import__(
            "apps.repositories.providers.github", fromlist=["extract_staged_archive"]
        ).extract_staged_archive(self.staged, staging_root=self.parent)
        self.extracted = self.extraction_context.__enter__()
        self.addCleanup(self.extraction_context.__exit__, None, None, None)
        self.commit = resolved_commit()
        self.manifest = build_repository_manifest(
            resolved_commit=self.commit,
            staged_archive=self.staged,
            extracted_archive=self.extracted,
        )

    def _resign(self, data):
        data = dict(data)
        data["files"] = tuple(data["files"])
        data["manifest_sha256"] = "0" * 64
        unsigned = RepositoryManifest.model_validate(data)
        data["manifest_sha256"] = compute_manifest_sha256(unsigned)
        return RepositoryManifest.model_validate(data)

    def test_successful_manifest_build_and_filesystem_verification(self):
        result = verify_manifest(
            self.manifest,
            extracted_archive=self.extracted,
            staged_archive=self.staged,
            resolved_commit=self.commit,
        )
        self.assertTrue(result.valid)
        self.assertEqual(self.manifest.source_completeness, "unverified")
        self.assertEqual(self.manifest.file_count, 2)
        self.assertEqual(self.manifest.total_bytes, sum(map(len, self.files.values())))
        self.assertEqual(self.manifest.archive_sha256, self.staged.sha256)
        by_path = {item.path: item for item in self.manifest.files}
        for path, content in self.files.items():
            self.assertEqual(by_path[path].size_bytes, len(content))
            self.assertEqual(by_path[path].sha256, hashlib.sha256(content).hexdigest())

    def test_canonical_output_digest_and_record_order_are_deterministic(self):
        reversed_inventory = type(self.extracted)(
            root=self.extracted.root,
            inventory=tuple(reversed(self.extracted.inventory)),
            file_count=self.extracted.file_count,
            total_bytes=self.extracted.total_bytes,
            source_archive_sha256=self.extracted.source_archive_sha256,
            commit_sha=self.extracted.commit_sha,
        )
        reordered = build_repository_manifest(
            resolved_commit=self.commit,
            staged_archive=self.staged,
            extracted_archive=reversed_inventory,
        )
        self.assertEqual(serialize_manifest(self.manifest), serialize_manifest(reordered))
        self.assertEqual(canonical_manifest_payload_bytes(self.manifest), canonical_manifest_payload_bytes(reordered))
        self.assertEqual(compute_manifest_sha256(self.manifest), compute_manifest_sha256(reordered))
        self.assertEqual([item.path for item in self.manifest.files], sorted(self.files))

    def test_content_commit_and_archive_changes_change_digest(self):
        base = self.manifest.model_dump(mode="json")
        self.assertNotEqual(self.manifest.manifest_sha256, self._resign({**base, "commit_sha": "b" * 40}).manifest_sha256)
        self.assertNotEqual(self.manifest.manifest_sha256, self._resign({**base, "archive_sha256": "c" * 64}).manifest_sha256)
        changed_files = [dict(item) for item in base["files"]]
        changed_files[0]["sha256"] = "d" * 64
        self.assertNotEqual(self.manifest.manifest_sha256, self._resign({**base, "files": changed_files}).manifest_sha256)

    def test_generated_at_is_not_part_of_contract_or_digest(self):
        data = self.manifest.model_dump(mode="json")
        data["generated_at"] = "2026-11-23T00:00:00Z"
        with self.assertRaises(ValidationError):
            RepositoryManifest.model_validate_json(json.dumps(data))
        self.assertNotIn(b"generated_at", serialize_manifest(self.manifest))

    def test_contract_rejects_missing_extra_version_hash_and_count_errors(self):
        base = self.manifest.model_dump(mode="json")
        for mutation in (
            lambda d: d.pop("repository"),
            lambda d: d.update(extra_field="unexpected"),
            lambda d: d.update(schema_version="repository-manifest.v9"),
            lambda d: d.update(commit_sha="short"),
            lambda d: d.update(archive_sha256="not-a-hash"),
            lambda d: d.update(digest_algorithm="sha-512"),
            lambda d: d.update(file_count=99),
            lambda d: d.update(total_bytes=999),
            lambda d: d["repository"].update(canonical_url="https://github.com/attacker/other"),
            lambda d: d["repository"].update(owner="../outside"),
        ):
            altered = copy.deepcopy(base)
            mutation(altered)
            with self.subTest(altered=altered), self.assertRaises(ValidationError):
                RepositoryManifest.model_validate_json(json.dumps(altered))

    def test_contract_rejects_bad_file_hash_sizes_duplicate_paths_and_collisions(self):
        base = self.manifest.model_dump(mode="json")
        bad_records = [
            [{**base["files"][0], "sha256": "bad"}, base["files"][1]],
            [{**base["files"][0], "size_bytes": -1}, base["files"][1]],
            [base["files"][0], base["files"][0]],
            [dict(base["files"][0], path="src/../main.py"), base["files"][1]],
            [dict(base["files"][0], path="/absolute.py"), base["files"][1]],
            [dict(base["files"][0], path="src\\main.py"), base["files"][1]],
            [dict(base["files"][0], path="src\\u0301.py"), base["files"][1]],
        ]
        for files in bad_records:
            altered = dict(base, files=files)
            altered["file_count"] = len(files)
            altered["total_bytes"] = sum(file["size_bytes"] for file in files if file["size_bytes"] >= 0)
            with self.subTest(files=files), self.assertRaises(ValidationError):
                RepositoryManifest.model_validate_json(json.dumps(altered))
        with self.assertRaises(ValidationError):
            ManifestFile(path="src\\u0301.py", size_bytes=1, sha256="a" * 64)

    def test_nfc_unicode_paths_are_accepted_and_decomposed_paths_rejected(self):
        accepted = ManifestFile(path="caf\u00e9.txt", size_bytes=0, sha256="0" * 64)
        self.assertEqual(accepted.path, "caf\u00e9.txt")
        with self.assertRaises(ValidationError):
            ManifestFile(path="cafe\u0301.txt", size_bytes=0, sha256="0" * 64)
        data = self.manifest.model_dump(mode="json")
        files = [dict(item) for item in data["files"]]
        files[0]["path"] = "caf\u00e9.txt"
        unicode_manifest = self._resign({**data, "files": files})
        encoded = serialize_manifest(unicode_manifest)
        self.assertIn("caf\u00e9.txt".encode("utf-8"), encoded)
        self.assertNotIn(b"caf\\u00e9.txt", encoded)

    def test_tampered_digest_and_malformed_or_oversized_inputs_fail_closed(self):
        data = self.manifest.model_dump(mode="json")
        data["manifest_sha256"] = "0" * 64
        result = verify_manifest(json.dumps(data))
        self.assertFalse(result.valid)
        self.assertEqual(result.checks[-1].error_code, "manifest_digest_mismatch")
        self.assertFalse(verify_manifest(b"{not json").valid)
        self.assertFalse(verify_manifest(b" " * (MAX_MANIFEST_BYTES + 1)).valid)
        with self.assertRaises(GitHubManifestIntegrityError):
            serialize_manifest(RepositoryManifest.model_validate_json(json.dumps(data)))

    def test_modified_missing_and_extra_files_fail_filesystem_verification(self):
        readme = self.extracted.root / "README.md"
        original = readme.read_bytes()
        readme.write_bytes(b"modified")
        self.assertFalse(verify_manifest(self.manifest, extracted_archive=self.extracted).valid)
        readme.write_bytes(original)
        readme.unlink()
        self.assertFalse(verify_manifest(self.manifest, extracted_archive=self.extracted).valid)
        readme.write_bytes(original)
        (self.extracted.root / "unexpected.txt").write_bytes(b"extra")
        self.assertFalse(verify_manifest(self.manifest, extracted_archive=self.extracted).valid)

    def test_re_signed_wrong_size_and_wrong_file_hash_fail_filesystem_check(self):
        base = self.manifest.model_dump(mode="json")
        files = [dict(item) for item in base["files"]]
        files[0]["size_bytes"] += 1
        altered_size = self._resign({**base, "files": files, "total_bytes": base["total_bytes"] + 1})
        self.assertFalse(verify_manifest(altered_size, extracted_archive=self.extracted).valid)
        files = [dict(item) for item in base["files"]]
        files[0]["sha256"] = "e" * 64
        altered_hash = self._resign({**base, "files": files})
        self.assertFalse(verify_manifest(altered_hash, extracted_archive=self.extracted).valid)

    def test_archive_repository_and_commit_linkage_are_verified(self):
        changed_archive = StagedArchive(
            path=self.staged.path,
            commit_sha=COMMIT,
            sha256="f" * 64,
            size_bytes=self.staged.size_bytes,
            content_type=self.staged.content_type,
        )
        self.assertFalse(verify_manifest(self.manifest, staged_archive=changed_archive).valid)
        other_repo = GitHubRepository(
            provider_repository_id="999", owner="other", name="repo",
            canonical_url="https://github.com/other/repo", visibility="public",
        )
        self.assertFalse(verify_manifest(self.manifest, resolved_commit=resolved_commit(repo=other_repo)).valid)
        self.assertFalse(verify_manifest(self.manifest, resolved_commit=resolved_commit("b" * 40)).valid)

    def test_verification_does_not_modify_files_or_disclose_local_paths(self):
        before = {path: (self.extracted.root / path).read_bytes() for path in self.files}
        outside = self.parent / "outside-sentinel.txt"
        outside.write_bytes(b"sentinel")
        result = verify_manifest(self.manifest, extracted_archive=self.extracted)
        self.assertTrue(result.valid)
        self.assertEqual({path: (self.extracted.root / path).read_bytes() for path in self.files}, before)
        self.assertEqual(outside.read_bytes(), b"sentinel")
        self.assertNotIn(str(self.parent), str(result))

    @unittest.skipIf(os.name == "nt", "Symlink creation requires platform privileges on Windows.")
    def test_unexpected_symlink_and_special_file_are_rejected_without_following(self):
        target = self.parent / "outside-sentinel.txt"
        target.write_bytes(b"unchanged")
        (self.extracted.root / "evil-link").symlink_to(target)
        self.assertFalse(verify_manifest(self.manifest, extracted_archive=self.extracted).valid)
        (self.extracted.root / "evil-link").unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.extracted.root / "unexpected-pipe")
            self.assertFalse(verify_manifest(self.manifest, extracted_archive=self.extracted).valid)
        self.assertEqual(target.read_bytes(), b"unchanged")

    def test_manifest_structure_rejects_file_directory_collision(self):
        base = self.manifest.model_dump(mode="json")
        files = [
            {"path": "node", "size_bytes": 0, "sha256": "1" * 64},
            {"path": "node/child", "size_bytes": 0, "sha256": "2" * 64},
        ]
        altered = dict(base, files=files, file_count=2, total_bytes=0)
        with self.assertRaises(ValidationError):
            RepositoryManifest.model_validate_json(json.dumps(altered))

    def test_unexpected_verification_limits_fail_with_structured_result(self):
        result = verify_manifest(self.manifest, extracted_archive=self.extracted, limits=None)
        self.assertFalse(result.valid)
        self.assertEqual(result.checks[0].error_code, "verification_limits_invalid")


if __name__ == "__main__":
    unittest.main()
