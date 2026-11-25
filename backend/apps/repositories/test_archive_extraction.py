"""Security and inventory tests for staged tarball extraction."""

import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.repositories.providers.github import (
    ArchiveExtractionLimits,
    ExtractedArchive,
    GitHubArchiveExtractionError,
    GitHubArchiveExtractionLimitExceeded,
    GitHubInvalidArchive,
    GitHubUnsafeArchiveMember,
    StagedArchive,
    extract_staged_archive,
)

COMMIT = "a" * 40


def build_tar_gz(members, *, corrupt_tail=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, kind, content, linkname in members:
            info = tarfile.TarInfo(name)
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                info.size = 0
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = linkname or "../../outside"
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = linkname or "../../outside"
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
            elif kind == "character":
                info.type = tarfile.CHRTYPE
            elif kind == "block":
                info.type = tarfile.BLKTYPE
            elif kind == "sparse":
                info.type = tarfile.GNUTYPE_SPARSE
            elif kind == "unknown":
                info.type = b"Z"
            else:
                info.type = tarfile.REGTYPE
                info.size = len(content)
            archive.addfile(info, io.BytesIO(content) if kind == "file" else None)
    body = output.getvalue()
    return body[:-8] if corrupt_tail else body


def staged(body, parent):
    path = Path(parent) / "source.tar.gz"
    path.write_bytes(body)
    return StagedArchive(
        path=path,
        commit_sha=COMMIT,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        content_type="application/x-gzip",
    )


def wrapper_files(*files):
    return [("untrusted-provider-wrapper/", "directory", b"", None), *files]


class ArchiveExtractionTests(unittest.TestCase):
    def test_extracts_nested_files_and_returns_deterministic_inventory(self):
        body = build_tar_gz(wrapper_files(
            ("untrusted-provider-wrapper/z/b.txt", "file", b"second", None),
            ("untrusted-provider-wrapper/a/nested.txt", "file", b"first file", None),
            ("untrusted-provider-wrapper/empty/", "directory", b"", None),
        ))
        with tempfile.TemporaryDirectory() as parent:
            artifact = staged(body, parent)
            with extract_staged_archive(artifact, staging_root=parent) as result:
                self.assertIsInstance(result, ExtractedArchive)
                self.assertEqual(result.file_count, 2)
                self.assertEqual(result.total_bytes, len(b"second") + len(b"first file"))
                self.assertEqual(result.commit_sha, COMMIT)
                self.assertEqual(result.source_completeness, "unverified")
                self.assertEqual([entry.relative_path for entry in result.inventory], ["a/nested.txt", "z/b.txt"])
                self.assertEqual(result.inventory[0].sha256, hashlib.sha256(b"first file").hexdigest())
                self.assertEqual(result.inventory[1].sha256, hashlib.sha256(b"second").hexdigest())
                self.assertEqual((result.root / "a" / "nested.txt").read_bytes(), b"first file")
                self.assertTrue((result.root / "empty").is_dir())
                extracted_root = result.root
            self.assertFalse(extracted_root.exists())
            self.assertEqual(list(Path(parent).glob("codewise-github-extract-*")), [])

    def test_mixed_posix_and_windows_separators_normalize_safely(self):
        body = build_tar_gz(wrapper_files(
            (r"untrusted-provider-wrapper\folder/nested\file.txt", "file", b"safe", None),
        ))
        with tempfile.TemporaryDirectory() as parent:
            with extract_staged_archive(staged(body, parent), staging_root=parent) as result:
                self.assertEqual(result.inventory[0].relative_path, "folder/nested/file.txt")
                self.assertEqual((result.root / "folder" / "nested" / "file.txt").read_bytes(), b"safe")

    def test_absolute_traversal_drive_unc_and_mixed_separator_paths_are_rejected(self):
        malicious = [
            ("/outside.txt", "file", b"bad", None),
            ("../outside.txt", "file", b"bad", None),
            ("root/../../outside.txt", "file", b"bad", None),
            (r"C:\outside.txt", "file", b"bad", None),
            (r"C:relative.txt", "file", b"bad", None),
            (r"\\server\share\outside.txt", "file", b"bad", None),
            (r"root\..\outside.txt", "file", b"bad", None),
            ("root//ambiguous.txt", "file", b"bad", None),
            ("root/./ambiguous.txt", "file", b"bad", None),
        ]
        for item in malicious:
            with self.subTest(path=item[0]), tempfile.TemporaryDirectory() as parent:
                outside = Path(parent).parent / "codewise-extraction-sentinel.txt"
                previous = outside.read_bytes() if outside.exists() else None
                try:
                    with self.assertRaises(GitHubUnsafeArchiveMember):
                        with extract_staged_archive(staged(build_tar_gz([item]), parent), staging_root=parent):
                            pass
                    self.assertEqual(outside.read_bytes() if outside.exists() else None, previous)
                finally:
                    if previous is None and outside.exists():
                        outside.unlink()

    def test_empty_and_malformed_member_paths_are_rejected(self):
        bad = [
            ("", "file", b"x", None),
            ("root/file.txt/", "file", b"x", None),
            ("root/bad\x01name", "file", b"x", None),
            ("root/name:stream", "file", b"x", None),
            ("root/NUL.txt", "file", b"x", None),
        ]
        for item in bad:
            with self.subTest(path=item[0]), tempfile.TemporaryDirectory() as parent:
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(staged(build_tar_gz([item]), parent), staging_root=parent):
                        pass

    def test_nul_path_component_is_rejected_by_path_validator(self):
        from apps.repositories.providers.github.extraction import _normalize_member_path

        with self.assertRaises(GitHubUnsafeArchiveMember):
            _normalize_member_path(
                "root/evil\x00.txt", "file", max_path_length=1024, max_path_depth=64,
            )

    def test_symlinks_hardlinks_and_special_types_are_rejected(self):
        attacks = [
            ("root/link", "symlink", b"", "../../outside"),
            ("root/link", "hardlink", b"", "../../outside"),
            ("root/unknown", "unknown", b"", None),
            ("root/device", "character", b"", None),
            ("root/device", "block", b"", None),
            ("root/pipe", "fifo", b"", None),
            ("root/sparse", "sparse", b"", None),
        ]
        for member in attacks:
            with self.subTest(kind=member[1]), tempfile.TemporaryDirectory() as parent:
                outside = Path(parent).parent / "codewise-link-sentinel.txt"
                outside.write_bytes(b"unchanged")
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(staged(build_tar_gz([member]), parent), staging_root=parent):
                        pass
                self.assertEqual(outside.read_bytes(), b"unchanged")
                outside.unlink()

    def test_duplicate_paths_and_file_directory_collisions_are_rejected(self):
        cases = [
            wrapper_files(
                ("untrusted-provider-wrapper/a.txt", "file", b"one", None),
                ("untrusted-provider-wrapper/a.txt", "file", b"two", None),
            ),
            wrapper_files(
                ("untrusted-provider-wrapper/node", "file", b"file", None),
                ("untrusted-provider-wrapper/node/child", "file", b"child", None),
            ),
            wrapper_files(
                ("untrusted-provider-wrapper/dir/", "directory", b"", None),
                ("untrusted-provider-wrapper/dir/", "directory", b"", None),
            ),
            wrapper_files(
                ("untrusted-provider-wrapper/a/b.txt", "file", b"one", None),
                (r"untrusted-provider-wrapper\a\b.txt", "file", b"two", None),
            ),
            wrapper_files(
                ("untrusted-provider-wrapper/A/file.txt", "file", b"one", None),
                ("untrusted-provider-wrapper/a/other.txt", "file", b"two", None),
            ),
        ]
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as parent:
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(staged(build_tar_gz(members), parent), staging_root=parent):
                        pass

    def test_file_directory_conflict_is_rejected_independent_of_member_order(self):
        cases = [
            wrapper_files(
                ("untrusted-provider-wrapper/a/b.txt", "file", b"child", None),
                ("untrusted-provider-wrapper/a", "file", b"parent", None),
            ),
            wrapper_files(
                ("untrusted-provider-wrapper/a", "directory", b"", None),
                ("untrusted-provider-wrapper/a", "file", b"parent", None),
            ),
        ]
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as parent:
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(staged(build_tar_gz(members), parent), staging_root=parent):
                        pass

    def test_file_total_member_path_and_depth_limits_are_enforced(self):
        files = wrapper_files(
            ("untrusted-provider-wrapper/dir/a.txt", "file", b"12345", None),
            ("untrusted-provider-wrapper/dir/b.txt", "file", b"67890", None),
        )
        cases = [
            ({"max_file_bytes": 4}, GitHubArchiveExtractionLimitExceeded),
            ({"max_total_bytes": 8}, GitHubArchiveExtractionLimitExceeded),
            ({"max_archive_uncompressed_bytes": 100}, GitHubArchiveExtractionLimitExceeded),
            ({"max_members": 2}, GitHubArchiveExtractionLimitExceeded),
            ({"max_path_length": 4}, GitHubArchiveExtractionLimitExceeded),
            ({"max_path_depth": 1}, GitHubArchiveExtractionLimitExceeded),
        ]
        for limits, expected in cases:
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as parent:
                with self.assertRaises(expected):
                    with extract_staged_archive(
                        staged(build_tar_gz(files), parent),
                        staging_root=parent,
                        limits=ArchiveExtractionLimits(**limits),
                    ):
                        pass

    def test_truncated_and_digest_changed_archives_are_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            truncated = staged(build_tar_gz(wrapper_files(
                ("untrusted-provider-wrapper/a.txt", "file", b"body", None),
            ), corrupt_tail=True), parent)
            with self.assertRaises(GitHubInvalidArchive):
                with extract_staged_archive(truncated, staging_root=parent):
                    pass

            valid = staged(build_tar_gz(wrapper_files(
                ("untrusted-provider-wrapper/a.txt", "file", b"body", None),
            )), parent)
            valid.path.write_bytes(b"changed")
            with self.assertRaises(GitHubInvalidArchive):
                with extract_staged_archive(valid, staging_root=parent):
                    pass

    def test_actual_stream_length_must_match_declared_tar_size(self):
        body = build_tar_gz(wrapper_files(
            ("untrusted-provider-wrapper/file.txt", "file", b"declared", None),
        ))
        with tempfile.TemporaryDirectory() as parent:
            artifact = staged(body, parent)
            with patch("tarfile.TarFile.extractfile", return_value=io.BytesIO(b"short")):
                with self.assertRaises(GitHubInvalidArchive):
                    with extract_staged_archive(artifact, staging_root=parent):
                        pass
            self.assertEqual(list(Path(parent).glob("codewise-github-extract-*")), [])

    def test_extraction_limits_contract_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            ArchiveExtractionLimits(max_members=0)
        with self.assertRaises(ValueError):
            ArchiveExtractionLimits(max_file_bytes=True)

    def test_partial_extraction_is_removed_when_a_later_file_write_fails(self):
        members = wrapper_files(
            ("untrusted-provider-wrapper/one.txt", "file", b"first", None),
            ("untrusted-provider-wrapper/two.txt", "file", b"second", None),
        )
        with tempfile.TemporaryDirectory() as parent:
            artifact = staged(build_tar_gz(members), parent)
            from apps.repositories.providers.github import extraction

            original = extraction._write_regular_file
            calls = 0

            def fail_second(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise GitHubArchiveExtractionError("injected write failure")
                return original(*args, **kwargs)

            with patch.object(extraction, "_write_regular_file", side_effect=fail_second):
                with self.assertRaises(GitHubArchiveExtractionError):
                    with extract_staged_archive(artifact, staging_root=parent):
                        pass
            self.assertEqual(list(Path(parent).glob("codewise-github-extract-*")), [])

    def test_existing_file_is_never_overwritten(self):
        members = wrapper_files(("untrusted-provider-wrapper/file.txt", "file", b"archive", None))
        with tempfile.TemporaryDirectory() as parent:
            artifact = staged(build_tar_gz(members), parent)
            from apps.repositories.providers.github import extraction

            original = extraction._write_regular_file
            observed = []

            def create_then_write(archive, member, root, relative_path, **kwargs):
                destination = root / relative_path
                destination.write_bytes(b"sentinel")
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    original(archive, member, root, relative_path, **kwargs)
                observed.append(destination.read_bytes())
                raise GitHubUnsafeArchiveMember("existing file was preserved")

            with patch.object(extraction, "_write_regular_file", side_effect=create_then_write):
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(artifact, staging_root=parent):
                        pass
            self.assertEqual(observed, [b"sentinel"])
            self.assertEqual(list(Path(parent).glob("codewise-github-extract-*")), [])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not exposed consistently on Windows.")
    def test_extracted_modes_are_private_and_non_executable(self):
        body = build_tar_gz(wrapper_files(
            ("untrusted-provider-wrapper/bin/run.sh", "file", b"#!/bin/sh\n", None),
        ))
        with tempfile.TemporaryDirectory() as parent:
            with extract_staged_archive(staged(body, parent), staging_root=parent) as result:
                self.assertEqual(stat.S_IMODE(result.root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((result.root / "bin").stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((result.root / "bin/run.sh").stat().st_mode), 0o600)

    def test_nfc_and_windows_reserved_name_collisions_are_rejected(self):
        cases = [
            wrapper_files(
                ("untrusted-provider-wrapper/caf\u00e9.txt", "file", b"one", None),
                ("untrusted-provider-wrapper/cafe\u0301.txt", "file", b"two", None),
            ),
            wrapper_files(("untrusted-provider-wrapper/CON", "file", b"device", None)),
            wrapper_files(("untrusted-provider-wrapper/trailing. ", "file", b"ambiguous", None)),
        ]
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as parent:
                with self.assertRaises(GitHubUnsafeArchiveMember):
                    with extract_staged_archive(staged(build_tar_gz(members), parent), staging_root=parent):
                        pass


if __name__ == "__main__":
    unittest.main()
