"""Unit tests for the isolated GitHub provider adapter; all HTTP is mocked."""

import json
import logging
import socket
import ssl
import hashlib
import gzip
import http.client
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from apps.repositories.providers.github import (
    GitHubClient,
    GitHubArchiveTooLarge,
    GitHubHTTPError,
    GitHubInvalidArchive,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubRedirectRejected,
    GitHubRedirectLoop,
    GitHubTLSFailure,
    GitHubTimeout,
    GitHubTransportError,
    GitHubRepository,
    InvalidGitHubURL,
    RepositoryNotPublic,
    ResolvedCommit,
    parse_repository_url,
    validate_full_commit_sha,
)
from apps.repositories.providers.github.client import GITHUB_API_HOST

SHA = "a" * 40
SHA2 = "b" * 40


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, raw_body=None):
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.body = raw_body if raw_body is not None else json.dumps(payload or {}).encode()
        self.offset = 0

    def getheader(self, name):
        return self.headers.get(name.lower())

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeConnection:
    def __init__(self, host, response, **kwargs):
        self.host = host
        self.response = response
        self.kwargs = kwargs
        self.sock = FakeSocket()
        self.request_data = None
        self.closed = False

    def request(self, method, path, headers):
        self.request_data = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class FakeConnectionFactory:
    def __init__(self, responses):
        self.responses = list(responses)
        self.connections = []

    def __call__(self, host, **kwargs):
        if host not in {GITHUB_API_HOST, "codeload.github.com"}:
            raise AssertionError("Provider attempted to connect to a non-allowlisted host.")
        response = self.responses.pop(0)
        connection = FakeConnection(host, response, **kwargs)
        self.connections.append(connection)
        return connection


def public_payload(owner="octo-org", name="demo"):
    return {
        "id": 1234,
        "owner": {"login": owner},
        "name": name,
        "private": False,
        "visibility": "public",
        "default_branch": "main",
        "html_url": f"https://github.com/{owner}/{name}",
    }


def make_client(responses, **kwargs):
    factory = FakeConnectionFactory(responses)
    slept = []
    client = GitHubClient(
        connection_factory=factory,
        sleep=slept.append,
        random_value=lambda: 0.0,
        retry_base_seconds=0,
        retry_jitter_seconds=0,
        **kwargs,
    )
    return client, factory, slept


def valid_tar_gzip():
    buffer = io.BytesIO()
    contents = b"safe source sample\n"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        item = tarfile.TarInfo("demo/README.md")
        item.size = len(contents)
        archive.addfile(item, io.BytesIO(contents))
    return buffer.getvalue()


def tar_gzip_files(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, contents in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(contents)
            archive.addfile(item, io.BytesIO(contents))
    return buffer.getvalue()


def resolved_public_commit():
    repository = GitHubRepository(
        provider_repository_id="1234",
        owner="octo-org",
        name="demo",
        canonical_url="https://github.com/octo-org/demo",
        visibility="public",
        default_branch="main",
    )
    return ResolvedCommit(repository=repository, requested_ref=SHA, ref_kind="commit", commit_sha=SHA)


def codeload_location(owner="octo-org", name="demo", sha=SHA):
    return f"https://codeload.github.com/{owner}/{name}/legacy.tar.gz/{sha}"


def archive_responses(body=None, *, content_type="application/x-gzip", content_length=True):
    body = valid_tar_gzip() if body is None else body
    download_headers = {"Content-Type": content_type}
    if content_length:
        download_headers["Content-Length"] = str(len(body))
    return [
        FakeResponse(status=302, headers={"Location": codeload_location()}),
        FakeResponse(status=200, raw_body=body, headers=download_headers),
    ]


class GitHubURLValidationTests(unittest.TestCase):
    def test_valid_canonical_url_and_git_suffix(self):
        locator = parse_repository_url("https://github.com/Octo-Org/demo.git/")
        self.assertEqual(locator.owner, "Octo-Org")
        self.assertEqual(locator.name, "demo")

    def test_invalid_urls_and_unsupported_hosts(self):
        bad_urls = [
            "http://github.com/owner/repo",
            "https://github.com.evil.test/owner/repo",
            "https://user@github.com/owner/repo",
            "https://github.com:444/owner/repo",
            "https://github.com/owner/repo/tree/main",
            "https://github.com/owner/repo?tab=readme",
            "https://github.com/owner/repo#readme",
            "https://github.com/owner/repo?",
            "https://github.com/owner/repo#",
            "https://github.com/owner%2Fevil/repo",
            "https://github.com//owner/repo",
            "https://github.com/owner/repo//",
            "https://github.com/owner/../repo",
            "https://github.com/owner/repo\\@evil.test",
        ]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(InvalidGitHubURL):
                parse_repository_url(url)

    def test_invalid_owner_and_repository_identifiers(self):
        for url in (
            "https://github.com/-owner/repo",
            "https://github.com/owner-/repo",
            "https://github.com/owner/invalid!repo",
            "https://github.com/owner/" + "r" * 101,
        ):
            with self.subTest(url=url), self.assertRaises(InvalidGitHubURL):
                parse_repository_url(url)

    def test_full_sha_validation_is_strict_and_normalizes_case(self):
        self.assertEqual(validate_full_commit_sha("A" * 40), "a" * 40)
        for sha in ("a" * 7, "g" * 40, "a" * 64, "a" * 39):
            with self.subTest(sha=sha), self.assertRaises(InvalidGitHubURL):
                validate_full_commit_sha(sha)


class GitHubClientTests(unittest.TestCase):
    def test_public_repository_lookup_is_anonymous_and_typed(self):
        client, factory, _ = make_client([FakeResponse(payload=public_payload())])
        result = client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(result.provider_repository_id, "1234")
        self.assertEqual(result.visibility, "public")
        self.assertEqual(result.default_branch, "main")
        method, path, headers = factory.connections[0].request_data
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/repos/octo-org/demo")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(factory.connections[0].kwargs["timeout"], 3.05)
        self.assertEqual(factory.connections[0].sock.timeouts, [10.0])

    def test_rejects_private_repository_from_public_lookup(self):
        payload = public_payload()
        payload["private"] = True
        payload["visibility"] = "private"
        client, _, _ = make_client([FakeResponse(payload=payload)])
        with self.assertRaises(RepositoryNotPublic):
            client.get_public_repository("https://github.com/octo-org/demo")

    def test_branch_reference_resolves_to_full_commit(self):
        payload = {"object": {"type": "commit", "sha": SHA}}
        client, factory, _ = make_client([FakeResponse(payload=public_payload()), FakeResponse(payload=payload)])
        repository = client.get_public_repository("https://github.com/octo-org/demo")
        resolved = client.resolve_branch(repository, "release/2026.1")
        self.assertEqual(resolved.commit_sha, SHA)
        self.assertEqual(resolved.ref_kind, "branch")
        self.assertEqual(factory.connections[1].request_data[1], "/repos/octo-org/demo/git/ref/heads/release/2026.1")

    def test_annotated_tag_is_followed_to_commit(self):
        tag_ref = {"object": {"type": "tag", "sha": SHA}}
        nested = {"object": {"type": "commit", "sha": SHA2}}
        client, factory, _ = make_client([
            FakeResponse(payload=public_payload()),
            FakeResponse(payload=tag_ref),
            FakeResponse(payload=nested),
        ])
        repository = client.get_public_repository("https://github.com/octo-org/demo")
        resolved = client.resolve_tag(repository, "v2.0.0")
        self.assertEqual(resolved.commit_sha, SHA2)
        self.assertEqual(factory.connections[2].request_data[1], f"/repos/octo-org/demo/git/tags/{SHA}")

    def test_commit_reference_requires_full_sha_and_verifies_returned_sha(self):
        client, factory, _ = make_client([
            FakeResponse(payload=public_payload()),
            FakeResponse(payload={"sha": SHA}),
        ])
        repository = client.get_public_repository("https://github.com/octo-org/demo")
        resolved = client.resolve_commit(repository, SHA.upper())
        self.assertEqual(resolved.commit_sha, SHA)
        self.assertEqual(factory.connections[1].request_data[1], f"/repos/octo-org/demo/commits/{SHA}")

        with self.assertRaises(InvalidGitHubURL):
            client.resolve_commit(repository, "a" * 8)

    def test_branch_must_point_directly_to_commit(self):
        client, _, _ = make_client([
            FakeResponse(payload=public_payload()),
            FakeResponse(payload={"object": {"type": "tree", "sha": SHA}}),
        ])
        repository = client.get_public_repository("https://github.com/octo-org/demo")
        from apps.repositories.providers.github import GitHubInvalidResponse
        with self.assertRaises(GitHubInvalidResponse):
            client.resolve_branch(repository, "main")

    def test_not_found_is_typed(self):
        client, _, _ = make_client([FakeResponse(status=404)])
        with self.assertRaises(GitHubNotFound):
            client.get_public_repository("https://github.com/octo-org/demo")

    def test_transient_http_failure_uses_bounded_retry(self):
        client, factory, slept = make_client([
            FakeResponse(status=503),
            FakeResponse(payload=public_payload()),
        ])
        self.assertEqual(client.get_public_repository("https://github.com/octo-org/demo").name, "demo")
        self.assertEqual(len(factory.connections), 2)
        self.assertEqual(slept, [0.0])

    def test_rate_limit_retry_after_is_honored_when_within_cap(self):
        client, factory, slept = make_client([
            FakeResponse(status=429, headers={"Retry-After": "2"}),
            FakeResponse(payload=public_payload()),
        ], max_retry_after_seconds=5)
        client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(slept, [2.0])
        self.assertEqual(len(factory.connections), 2)

    def test_rate_limit_beyond_cap_is_returned_without_early_retry(self):
        client, factory, slept = make_client([
            FakeResponse(status=429, headers={"Retry-After": "120"}),
        ], max_retry_after_seconds=5)
        with self.assertRaises(GitHubRateLimited) as raised:
            client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(raised.exception.retry_after_seconds, 120.0)
        self.assertEqual(len(factory.connections), 1)
        self.assertEqual(slept, [])

    def test_timeout_retries_then_returns_typed_timeout(self):
        class TimeoutFactory:
            def __init__(self):
                self.calls = 0

            def __call__(self, host, **kwargs):
                self.calls += 1
                raise socket.timeout("sensitive upstream details")

        factory = TimeoutFactory()
        slept = []
        client = GitHubClient(connection_factory=factory, sleep=slept.append, max_attempts=2)
        with self.assertRaises(GitHubTimeout):
            client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(factory.calls, 2)
        self.assertEqual(len(slept), 1)

    def test_tls_validation_failure_is_not_retried(self):
        class TLSFailureFactory:
            calls = 0

            def __call__(self, host, **kwargs):
                self.calls += 1
                raise ssl.SSLError("certificate validation failed")

        factory = TLSFailureFactory()
        slept = []
        client = GitHubClient(connection_factory=factory, sleep=slept.append, max_attempts=3)
        with self.assertRaises(GitHubTLSFailure):
            client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(factory.calls, 1)
        self.assertEqual(slept, [])

    def test_redirect_is_rejected_and_never_followed(self):
        client, factory, _ = make_client([
            FakeResponse(status=302, headers={"Location": "https://attacker.invalid/steal"}),
        ])
        with self.assertRaises(GitHubRedirectRejected):
            client.get_public_repository("https://github.com/octo-org/demo")
        self.assertEqual(len(factory.connections), 1)
        self.assertEqual(factory.connections[0].kwargs["timeout"], 3.05)

    def test_error_logging_never_includes_response_secrets(self):
        secret = "ghs_super_secret_token_value"
        client, _, _ = make_client([
            FakeResponse(status=500, raw_body=f"Bearer {secret}".encode()),
        ], max_attempts=1)
        with self.assertLogs("apps.repositories.providers.github.client", level=logging.WARNING) as logs:
            with self.assertRaises(GitHubHTTPError):
                client.get_public_repository("https://github.com/octo-org/demo")
        self.assertNotIn(secret, "\n".join(logs.output))


class GitHubArchiveTests(unittest.TestCase):
    def test_success_streams_hashes_and_cleans_temporary_archive(self):
        body = valid_tar_gzip()
        with tempfile.TemporaryDirectory() as staging_root:
            client, factory, _ = make_client(
                archive_responses(body), archive_staging_root=staging_root
            )
            with client.stage_public_archive(resolved_public_commit()) as archive:
                self.assertTrue(archive.path.is_file())
                self.assertEqual(archive.path.read_bytes(), body)
                self.assertEqual(archive.sha256, hashlib.sha256(body).hexdigest())
                self.assertEqual(archive.size_bytes, len(body))
                self.assertEqual(archive.commit_sha, SHA)
                self.assertEqual(archive.source_completeness, "unverified")
                staged_path = archive.path
            self.assertFalse(staged_path.exists())
            self.assertEqual(list(Path(staging_root).iterdir()), [])

            self.assertEqual([c.host for c in factory.connections], [GITHUB_API_HOST, "codeload.github.com"])
            self.assertEqual(
                factory.connections[0].request_data[1],
                f"/repos/octo-org/demo/tarball/{SHA}",
            )
            api_headers = factory.connections[0].request_data[2]
            codeload_headers = factory.connections[1].request_data[2]
            self.assertNotIn("Authorization", api_headers)
            self.assertNotIn("Cookie", api_headers)
            self.assertNotIn("Authorization", codeload_headers)
            self.assertNotIn("Cookie", codeload_headers)

    def test_archive_result_does_not_claim_complete_git_tree(self):
        # A source archive may omit submodule contents, carry only Git LFS pointers,
        # or omit committed files marked export-ignore. Acquisition must preserve
        # that uncertainty instead of claiming the full Git tree was retrieved.
        contents = {
            "demo/.gitmodules": b'[submodule "vendor/lib"]\npath = vendor/lib\n',
            "demo/assets/model.bin": (
                b"version https://git-lfs.github.com/spec/v1\n"
                b"oid sha256:" + b"c" * 64 + b"\nsize 400\n"
            ),
            "demo/.gitattributes": b"generated/ export-ignore\n",
            "demo/README.md": b"project source\n",
        }
        body = tar_gzip_files(contents)
        with tempfile.TemporaryDirectory() as root:
            client, _, _ = make_client(archive_responses(body), archive_staging_root=root)
            with client.stage_public_archive(resolved_public_commit()) as archive:
                self.assertEqual(archive.source_completeness, "unverified")
                self.assertFalse((archive.path.parent / "extracted").exists())
                with tarfile.open(archive.path, mode="r:gz") as downloaded:
                    members = {member.name for member in downloaded.getmembers()}
                    pointer = downloaded.extractfile("demo/assets/model.bin").read()
                self.assertIn("demo/.gitmodules", members)
                self.assertIn("demo/assets/model.bin", members)
                self.assertNotIn("demo/vendor/lib/source.py", members)
                self.assertNotIn("demo/generated/bundle.js", members)
                self.assertIn(b"git-lfs.github.com/spec/v1", pointer)


    def test_archive_requires_resolved_commit_contract_and_full_sha(self):
        client, factory, _ = make_client([])
        with self.assertRaises(InvalidGitHubURL):
            with client.stage_public_archive("a" * 8):
                pass
        self.assertEqual(factory.connections, [])

    def test_redirect_host_scheme_port_and_path_are_strictly_validated(self):
        invalid_locations = [
            "https://evil.example/octo-org/demo/legacy.tar.gz/" + SHA,
            "http://codeload.github.com/octo-org/demo/legacy.tar.gz/" + SHA,
            "https://codeload.github.com:444/octo-org/demo/legacy.tar.gz/" + SHA,
            "https://user@codeload.github.com/octo-org/demo/legacy.tar.gz/" + SHA,
            "https://codeload.github.com/octo-org/other/legacy.tar.gz/" + SHA,
            "https://codeload.github.com/octo-org/demo/legacy.tar.gz/" + SHA2,
            "https://codeload.github.com/octo-org/demo/legacy.tar.gz/" + SHA + "?token=secret",
        ]
        for location in invalid_locations:
            with self.subTest(location=location), tempfile.TemporaryDirectory() as root:
                client, factory, _ = make_client(
                    [FakeResponse(status=302, headers={"Location": location})],
                    archive_staging_root=root,
                )
                with self.assertRaises(GitHubRedirectRejected):
                    with client.stage_public_archive(resolved_public_commit()):
                        pass
                self.assertEqual(len(factory.connections), 1)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_redirect_loop_is_detected(self):
        location = codeload_location()
        responses = [
            FakeResponse(status=302, headers={"Location": location}),
            FakeResponse(status=302, headers={"Location": location}),
        ]
        client, factory, _ = make_client(responses)
        with self.assertRaises(GitHubRedirectLoop):
            with client.stage_public_archive(resolved_public_commit()):
                pass
        self.assertEqual(len(factory.connections), 2)

    def test_oversized_stream_is_rejected_even_without_content_length_and_cleaned(self):
        body = valid_tar_gzip()
        with tempfile.TemporaryDirectory() as root:
            client, _, _ = make_client(
                archive_responses(body, content_length=False), archive_staging_root=root
            )
            with self.assertRaises(GitHubArchiveTooLarge):
                with client.stage_public_archive(
                    resolved_public_commit(), max_download_bytes=len(body) - 1
                ):
                    pass
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_invalid_content_type_and_corrupt_gzip_are_rejected(self):
        cases = [
            archive_responses(valid_tar_gzip(), content_type="text/html"),
            archive_responses(b"<html>not a tarball</html>"),
            archive_responses(gzip.compress(b"not a tar header")),
            archive_responses(valid_tar_gzip()[:-8]),
        ]
        for responses in cases:
            with self.subTest(responses=responses), tempfile.TemporaryDirectory() as root:
                client, _, _ = make_client(responses, archive_staging_root=root)
                with self.assertRaises(GitHubInvalidArchive):
                    with client.stage_public_archive(resolved_public_commit()):
                        pass
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_declared_content_length_must_match_streamed_bytes(self):
        body = valid_tar_gzip()
        responses = [
            FakeResponse(status=302, headers={"Location": codeload_location()}),
            FakeResponse(status=200, raw_body=body, headers={
                "Content-Type": "application/x-gzip",
                "Content-Length": str(len(body) + 1),
            }),
        ]
        with tempfile.TemporaryDirectory() as root:
            client, _, _ = make_client(responses, archive_staging_root=root)
            with self.assertRaises(GitHubTransportError):
                with client.stage_public_archive(resolved_public_commit()):
                    pass
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_uncompressed_validation_limit_is_enforced_without_extracting(self):
        body = valid_tar_gzip()
        with tempfile.TemporaryDirectory() as root:
            client, _, _ = make_client(archive_responses(body), archive_staging_root=root)
            with self.assertRaises(GitHubArchiveTooLarge):
                with client.stage_public_archive(
                    resolved_public_commit(), max_uncompressed_bytes=16
                ):
                    pass
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_interrupted_and_timed_out_streams_fail_and_clean_staging(self):
        class InterruptedResponse(FakeResponse):
            def __init__(self, timeout=False):
                super().__init__(status=200, raw_body=valid_tar_gzip(), headers={
                    "Content-Type": "application/x-gzip",
                })
                self.calls = 0
                self.timeout = timeout

            def read(self, size=-1):
                self.calls += 1
                if self.calls == 1:
                    return self.body[:32]
                if self.timeout:
                    raise socket.timeout("secret timeout detail")
                raise http.client.IncompleteRead(b"partial")

        for response, expected in (
            (InterruptedResponse(), GitHubTransportError),
            (InterruptedResponse(timeout=True), GitHubTimeout),
        ):
            with self.subTest(error=expected.__name__), tempfile.TemporaryDirectory() as root:
                responses = [
                    FakeResponse(status=302, headers={"Location": codeload_location()}),
                    response,
                ]
                client, _, _ = make_client(responses, archive_staging_root=root)
                with self.assertRaises(expected):
                    with client.stage_public_archive(resolved_public_commit()):
                        pass
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_http_errors_and_rate_limits_use_shared_typed_policy(self):
        client, _, _ = make_client([FakeResponse(status=404)])
        with self.assertRaises(GitHubNotFound):
            with client.stage_public_archive(resolved_public_commit()):
                pass

        rate_limit = FakeResponse(status=429, headers={"Retry-After": "0"})
        client, factory, slept = make_client([rate_limit, *archive_responses()])
        with client.stage_public_archive(resolved_public_commit()) as archive:
            self.assertTrue(archive.path.exists())
        self.assertEqual(len(factory.connections), 3)
        self.assertEqual(slept, [0.0])

    def test_redirect_secrets_are_not_sent_or_included_in_errors(self):
        secret = "temporary-secret-credential"
        location = f"https://evil.example/archive?token={secret}"
        client, factory, _ = make_client([
            FakeResponse(status=302, headers={"Location": location}),
        ])
        with self.assertRaises(GitHubRedirectRejected) as raised:
            with client.stage_public_archive(resolved_public_commit()):
                pass
        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(len(factory.connections), 1)
        self.assertFalse(any(
            name.lower() in {"authorization", "cookie", "proxy-authorization"}
            for name in factory.connections[0].request_data[2]
        ))


if __name__ == "__main__":
    unittest.main()
