# GitHub Repository Integration and Ingestion Design

**Status:** Proposed end-to-end design with public metadata/ref lookup, bounded archive acquisition, safe temporary extraction, canonical GitHub manifest verification, and provider-independent local snapshot storage/sealing implemented. GitHub App/private access, repository endpoints, inclusion/filtering policy, production object storage, and agent execution/content-read integration remain unimplemented. This document supplements [repository-workflow.md](repository-workflow.md) and [repository-ingestion-backend.md](repository-ingestion-backend.md). [ADR-002](adr-002-github-source-acquisition.md) records the accepted commit-pinned acquisition direction. Existing eight-agent/group-selection and workspace/snapshot contracts remain authoritative.

## 1. System boundary and data flow

```text
Browser
  -> Django REST API (/api/v1/, user session/platform auth)
  -> workspace authorization + submission service
  -> backend/apps/repositories/providers/github/ isolated provider adapter
       -> public: anonymous GitHub REST/archive requests
       -> private: grant check -> credential broker -> scoped read token
  -> bounded archive staging -> safe extraction/filtering -> file hashing
  -> canonical manifest + integrity validation -> private SnapshotStorage
  -> sealed workspace RepositorySnapshot record
  -> later orchestration binds one snapshot ref to one execution
  -> Agent Foundation gives each selected agent authorized bounded reads
```

Current implementation facts: `backend/apps/repositories/` implements the custom `User`; workspace and membership records/services; workspace-scoped repository, grant, submission, and snapshot metadata; audit events; execution-to-snapshot binding metadata; strict Pydantic contracts; and domain authorization/record services. Its GitHub provider implements strict public identity parsing, anonymous metadata/ref lookup, full-SHA resolution, bounded HTTP behavior, public commit-pinned archive acquisition, safe temporary extraction, and `repository-manifest.v1` canonical integrity verification. The provider-independent `storage/` package defines a storage protocol and private local filesystem implementation. `snapshot_service.py` verifies source/manifest linkage, stores and verifies manifest-listed regular files, atomically publishes the local directory, seals only after verification, authorizes content reads, verifies stored integrity, and provides retention/deletion/recovery operations. Archive SHA-256, file SHA-256, and manifest SHA-256 cover different bytes; none proves complete or faithful Git-tree retrieval. `source_completeness` remains `unverified`. There is no repository HTTP API, OAuth/App setup, private provider acquisition, source inclusion/filtering policy, production object-storage adapter, or agent execution/content-read integration. Django uses SQLite locally and denies unauthenticated API access by default. These pieces do not yet make an end-to-end submission or analysis workflow available.

Owners: `backend/apps/repositories/` owns implemented ORM/domain records, workspace/object authorization checks, provider-independent snapshot coordination, and storage services. The GitHub adapter, temporary extraction, GitHub archive manifest/integrity implementation, and GitHub-to-storage manifest adapter are isolated under `providers/github/`. Repository HTTP/API transport is not implemented. `repository_engine/` remains the planned owner of provider-neutral inclusion/filtering policy and multi-provider ingestion coordination. A future credential broker will resolve secrets only for a provider adapter. `orchestration/` will own execution state and pin the snapshot; `agent_foundation/` will expose a future read-only capability. Agents never import provider clients, credential references, Django querysets, or storage clients. Publishing remains a separate service and separate permission grant.

## 2. Public and private workflows

### Public repository

1. Accept a validated GitHub repository locator in a workspace-scoped submission. Platform account authentication may be required to save the workspace submission, but GitHub account authentication is not required to inspect a public repository. Public retrieval itself uses no GitHub credentials.
2. Resolve canonical provider repository ID and confirm the repository is public from provider metadata. A submitted URL alone never establishes existence or visibility.
3. Resolve the supplied branch/tag/full-SHA reference to one full commit SHA. Record the original ref for provenance; use the SHA thereafter.
4. Download the REST tar archive pinned to that commit SHA. Anonymous requests are used for public data.
5. Safely extract, filter, hash, build the manifest and verify storage writes. Seal only after all validation succeeds.
6. Orchestration starts only against the sealed snapshot. Mandatory Agents 1–2 and the selected optional groups from `repository-workflow.md` bind to the same snapshot ID and commit SHA. Patch authoring/testing/review remain local; GitHub write authorization is only needed if the user later explicitly publishes.

### Private repository

1. Do not disclose repository existence from an anonymous 404. Return a generic access-required/unverified state and offer the separate GitHub authorization flow.
2. After a future GitHub App authorization, verify that the selected installation and repository ID match the workspace grant and that effective permission includes repository Contents: read. Do this before private metadata, ref, commit, or archive retrieval.
3. Mint a short-lived installation access token restricted to the specific repository and only Contents: read. Never request write or PR permissions in this path. Do not store token material in task records; broker obtains it just in time.
4. Resolve the full SHA and obtain the private archive through the authorized provider adapter. Never send a token to agents/browser. A temporary archive redirect URL is treated as a secret and kept only in the worker memory for the bounded transfer.
5. Seal the source in workspace-isolated private storage. Revalidate grant state and workspace membership at snapshot-read/execution boundaries. Expired, revoked, suspended, mismatched, or uncertain grants fail closed. Revocation during active work cancels at the next safe checkpoint and blocks further content reads.

GitHub App installation authorization is the preferred automation grant. GitHub App user authorization is a separate user-bound mechanism useful for account identity, installation discovery, and user consent; it does not itself prove the installation has access to the exact repository. Validate effective access as the intersection of the user's repository access and the installation's repository selection/permissions. GitHub documents that installation tokens can be restricted to selected repositories and a subset of granted permissions, expire after one hour, and otherwise inherit the installation's permissions; therefore always request explicit repository IDs and minimum permissions. Do not assume an installation grant and user authorization token are interchangeable.

Permission boundary:

| Operation | Permission/grant |
|---|---|
| Public metadata/ref/archive | Anonymous; no GitHub account required |
| Private metadata/ref/archive | Exact workspace repository grant; Contents: read |
| Create patch locally, sandbox-test, review | No GitHub write permission; sandbox policy is separate |
| Create branch/push approved patch | Publishing service only; Contents: write, after explicit approval |
| Create pull request | Publishing service only; Pull requests: write, after explicit approval |

## 3. Reference resolution and retrieval

Use the GitHub REST Git References endpoint for `heads/{branch}` and `tags/{tag}`. For annotated tags, follow tag objects with a strict maximum depth and resolve to a commit object; reject trees/blobs and cycles. Alternatively use the commits API, which accepts a commit SHA, branch, or tag; implementation must confirm semantics and avoid ambiguous prefix matching. Require a full SHA-1 (40 hex) from GitHub today; retain an extensible validator for future provider hash formats only through a versioned contract change. Never use a short SHA as immutable identity.

After resolving, the provider calls the repository tarball endpoint with that full SHA as `ref`, never with a mutable branch/tag. GitHub's documented endpoint returns a `302`; a live anonymous public request observed a redirect to `codeload.github.com` and an `application/x-gzip` response. The current client allows exactly one redirect to HTTPS `codeload.github.com` on the default/443 port and validates the owner, repository, archive path, and full SHA in the target. It rejects other hosts, schemes, ports, query/fragment/userinfo, loops, and additional redirects. No credentials or cookies are attached to either hop. GitHub documents private archive links as temporary (five-minute expiry); private access is not implemented. Keep the allowlist explicit and revalidate it against GitHub behavior when the provider changes. Commit pinning does not establish archive completeness.

The Task 6C HTTP acquisition module and Task 6D extraction module have separate contracts and lifecycles: extraction consumes a `StagedArchive` yielded by the acquisition context and makes no HTTP requests. It validates the staged digest, fully checks gzip integrity, preflights tar members, then accepts only regular files and directories. It rejects absolute, drive/UNC, traversal, malformed or ambiguous paths, portable-name collisions, duplicate paths, links and all special/unknown types. It never trusts the archive wrapper as the local extraction root. `ArchiveExtractionLimits` is a frozen validated contract with defaults of 1 GiB total extracted file bytes, 64 MiB per file, 20,000 tar members, 1,024 UTF-8 bytes per normalized repository path, depth 64, 2 GiB decompressed archive bytes, 64 KiB PAX metadata per member, and 1 MiB total PAX metadata. Limits are checked against declarations during preflight and against actual streamed bytes during writes; written files are read back to verify inventory size and SHA-256. These starting values are configurable and not corpus-tuned.

Only tar regular files and directories are supported. Symbolic links, hard links, character/block devices, FIFOs, sparse entries, sockets, and unknown types fail the extraction. On POSIX systems with `dir_fd` support, directory traversal and file creation/read-back are descriptor-relative and use `O_NOFOLLOW`; files are created exclusively. Ownership and archive mode bits are ignored, and output files are non-executable (POSIX mode 0600, directories/root 0700). Extraction happens in a newly created `TemporaryDirectory`; failures and normal context exit clean it up. On POSIX, the extraction root is chmod 0700 and children are created private. On Windows, this implementation uses path-based checks and exclusive file creation, does not establish a custom restrictive ACL, and cannot provide the same no-follow/descriptor-relative guarantees; inherited ACLs and same-account concurrent filesystem interference remain platform/deployment considerations. The implementation reduces path-redirection risk but does not claim identical race protection on all platforms.

The result exposes a stable path-sorted inventory of normalized relative path, actual file size and SHA-256 of bytes read back from each extracted file, plus file count, total bytes, source archive digest, commit SHA and `source_completeness="unverified"`. It exposes no absolute extraction paths in inventory and is deleted with its extraction context. The separate manifest module turns that inventory into the versioned GitHub manifest contract described below. It does not filter source, compare against the Git tree, or prove source completeness. Provider archive bytes may omit or differ from original Git tree data, including submodule contents, Git LFS objects versus pointer files, `.gitattributes` `export-ignore` and `export-subst` effects, generated/excluded content, and metadata; a matching SHA is not proof of complete source retrieval. Any future manifest policy must identify omissions. Do not assume a tarball is an exact Git object database or that its root directory name is stable.

### Versioned manifest and integrity foundation

`providers/github/manifest.py` defines immutable Pydantic v2 `repository-manifest.v1` contracts and strict repository-relative file records. Paths must already be NFC-normalized UTF-8, use `/`, and pass traversal, absolute/drive, control-character, portable-name, duplicate, case-fold collision, and file/directory conflict checks. The builder sorts file records by normalized path and derives count and total bytes from those records; it verifies the active extracted filesystem before returning a manifest. No generated timestamp or random identifier is included; the deterministic manifest SHA-256 serves as content identity.

Canonicalization is `codewise-canonical-json-v1`: Pydantic JSON-mode values serialized as UTF-8, sorted object keys, compact `,` and `:` separators, non-ASCII characters emitted directly, integer sizes only, and NaN/Infinity forbidden. The SHA-256 digest input is the canonical manifest payload with `manifest_sha256` excluded; the final serialized manifest includes that digest and uses the same canonical JSON rules. Schema version, canonicalization identifier, digest algorithm and digest scope are explicit fields. Future incompatible schema or canonicalization changes require a new version identifier; readers must reject versions they do not support. Timestamps are absent and therefore cannot change the digest.

Keep the three digest meanings separate: **archive SHA-256** hashes the exact downloaded compressed archive bytes; **file SHA-256** hashes each actual extracted file’s bytes read back from disk; **manifest SHA-256** hashes the canonical manifest payload excluding its own digest field. Verification returns structured path-redacted checks for manifest structure/totals, canonical digest, optional staged archive and resolved repository/commit linkage, and, when the extraction context is active, the complete regular-file set, no-follow file types, sizes and recomputed hashes. Manifest input is bounded to 32 MiB and 20,000 file records. The verifier never writes files. A valid manifest or digest does not establish that GitHub’s archive represents the complete Git tree.

## 4. Ingestion and archive defense

The acquisition worker is isolated from agents and has no ability to execute a repository command. It performs only byte transfer, archive parsing, deterministic path policy, and hashing. Never invoke git, shell, package managers, build/test commands, hooks, repository code, dynamic import, or repo-defined ignore rules during ingestion.

Enforce configurable limits at ingress and while streaming: overall deadline, connect/read timeout, redirects, compressed bytes, decompressed bytes, entry count, per-file bytes, total accepted bytes, path length/depth, and concurrent work per workspace/repository. Numeric limits are deployment/product decisions based on measured corpus sizes. Stop work on cancellation; close response and delete/quarantine temporary content.

For each tar entry, normalize path separators and reject: absolute POSIX paths, drive/UNC paths, `.`/`..` traversal, NUL/control chars, paths escaping staging root, duplicate normalized paths, case-fold collisions, links, devices, FIFOs, sockets, sparse files, malformed headers, excessive PAX metadata, and unsupported entry types. Do not follow symlinks or hardlinks. The GitHub extractor implements this strict fail-closed type/path policy in a fresh disposable directory with exclusive file creation. The provider-level v1 manifest and verification foundation is implemented; cross-provider inclusion policy and end-to-end ingestion remain unimplemented. Unsafe or ambiguous entries fail extraction rather than being silently dropped as a complete source.

Use a versioned deterministic inclusion/exclusion policy. Preserve source-relevant text/code, manifests and configuration as defined by policy. Exclude VCS internals and explicitly classified generated/oversized/binary or secret-risk artifacts only under documented rules; represent every exclusion with reason code and path/digest metadata in the private manifest. Do not silently claim a complete repository when files were excluded. Do not parse or execute untrusted serialized formats. Treat source contents and metadata as adversarial prompt-injection data when later passed to agents.

## 5. Snapshot, storage, and lifecycle

The implemented `SnapshotStorage` protocol and local adapter are provider-independent and live under `backend/apps/repositories/storage/`. The configured absolute storage root must remain outside static/media roots and web-server document roots. Opaque workspace and snapshot UUIDs select `workspaces/<workspace>/snapshots/<snapshot>/manifest.json` and `files/<relative path>`; writes use private staging, exclusive file creation, digest verification, and same-filesystem atomic rename. No public URL or storage path is returned. The local adapter is for development and is not encrypted by application code; host permissions and disk encryption govern at-rest protection. A private object-storage adapter remains future production work.

The lifecycle service requires membership and current verified `contents_read` authorization for private/internal captures. It verifies the active extraction, repository identity, full commit SHA, archive digest linkage, canonical manifest, exact stored file set, sizes and per-file hashes before sealing. The existing `repository-manifest.v1` is persisted unchanged; no competing snapshot manifest or completeness claim is introduced. The separate `content_digest` is a versioned aggregate over sorted paths, lengths, and file hashes. Snapshot model/service guards prevent normal application mutation of identity and sealed content fields, queryset/bulk operations, and hard deletion; privileged SQL and host administrators can bypass these protections or alter files. Reads require membership, sealed state, repository association and applicable current grant, and are available only through controlled manifest/file/inventory/verification services.

The local publish and database seal are not one atomic transaction. A crash before rename leaves staging data eligible for age-based cleanup; a crash after rename but before database commit leaves an unsealed, inaccessible final directory. `recover_published_snapshot` verifies the canonical manifest, row metadata and all stored bytes before sealing. Retries reuse only byte-identical inventory/manifest content; collisions are rejected. This supports recovery but is not full cross-resource atomicity. POSIX roots/directories use mode 0700 and files 0600 with no-follow source opens where supported. Windows inherits filesystem ACLs and uses path-based checks; it does not provide equivalent custom ACL, race, or no-follow guarantees. Configure the root outside web-server document roots and protect it through host access controls.

Private-source retention defaults to 30 days after completed analysis; sealing time is the fallback anchor when there is no completed analysis. Workspace configuration is bounded to 1–3650 days. Existing execution bindings have no completion state, so any binding conservatively blocks expiry/deletion and may retain completed work. Deletion is retryable and records minimal audit metadata. Production object storage, backup purge schedules, and reconciliation of orphaned final directories remain future work. No partial ingest can become sealed.

## 6. API proposals

Transport remains Django REST Framework under `/api/v1/`. These are proposals, not endpoints. Use DRF serializers at HTTP boundaries and Pydantic strict contracts for provider responses/domain service exchanges. Authenticate CodeWise users as needed for workspace operations; GitHub auth is not required for public source. Every workspace query checks membership and object scope. Return generic not-found/access-required responses where private-repository enumeration is possible. Never expose tokens, temporary archive URLs, storage refs, private source, upstream bodies, or tracebacks.

### Submission and ref resolution

`POST /api/v1/repositories/submissions/`

```json
{
  "url": "https://github.com/owner/repo",
  "ref": "main",
  "idempotency_key": "client-generated-opaque-value"
}
```

`ref` is optional; if omitted, resolve the metadata-confirmed default branch. Accept only HTTPS github.com repository locator syntax, normalize one optional `.git`/trailing slash, reject userinfo, custom ports, query/fragment, non-GitHub hosts and tree/blob paths. Treat `ref` as data, encode each path segment, reject control characters and oversized refs, and only allow branch, tag, or full commit inputs; don't permit arbitrary URL.

Response `202`:

```json
{
  "submission_id": "opaque-id",
  "status": "resolving",
  "repository": null,
  "requested_ref": "main",
  "resolved_commit_sha": null,
  "access": {"status": "not_checked"},
  "snapshot": null,
  "correlation_id": "opaque-id"
}
```

Poll `GET /api/v1/repositories/submissions/{submission_id}/`. Return canonical repository/visibility only after authorized verification, full SHA after resolution, and sealed snapshot ref only when verified. Status values should distinguish `resolving`, `awaiting_authorization`, `retrieving`, `validating`, `sealed`, `failed`, `cancelled`. Existing `RepositorySubmission` schema may need a `requested_ref` and resolved SHA field before this contract is implemented; add through reviewed migration only.

### Private authorization initiation

Future `POST /api/v1/github/authorizations/` may accept `{ "workspace_id": "…", "repository_id": "…", "purpose": "repository_read", "return_path": "…" }` and return only `{ "authorization_url": "…", "state_expires_at": "…" }`. It must be CSRF-protected, session-bound, state-nonce protected, exact-redirect allowlisted, single-use, short-lived, and scoped to exact purpose/workspace/repository. This design does not implement OAuth or endpoint. Callback must verify platform session/state, bind grant to workspace and provider repository ID, then perform a provider permission/access check before setting `verified_read`.

### Future execution creation contract

`POST /api/v1/executions/` accepts submission ID, sealed snapshot ID, group IDs, and idempotency key. Server verifies workspace membership, snapshot sealed/integrity status, current private grant validity, and stable group IDs; it always adds mandatory `structure_analyzer` and `business_logic_analyst`. Persist one immutable `ExecutionSnapshotBinding` before enqueue. All selected agents receive only that binding and bounded `SnapshotContentReader`. No endpoint is being implemented as part of this design.

### Errors

Use sanitized `{ "error": {"code":"provider_rate_limited","message":"Repository service is temporarily rate limited.","retryable":true,"correlation_id":"…"} }`. Map invalid input 400, unauthenticated CodeWise caller 401, workspace permission denied 403/404 privacy policy, stale state/idempotency 409, local/platform rate limit 429, inaccessible private repo generic 404/authorization-required response, provider unavailable 502/503, integrity/path/limit errors to stable safe domain codes. Do not relay GitHub response bodies.

## 7. Timeouts, retries, rate limits, cancellation

- Bound every provider call and complete ingestion by one overall deadline; propagate cancellation through HTTP stream, extraction, hashing, storage and worker state.
- Retry only transient connection failures and selected 5xx, with bounded exponential backoff + jitter under the original deadline. Do not retry bad refs, 404/permission denied, unsafe archive, size excess, invalid manifest, or digest mismatch.
- For GitHub 403/429 rate-limit responses, honor valid `Retry-After`; if primary quota remaining is zero wait until `X-RateLimit-Reset`; for secondary rate limiting without headers wait at least one minute then bounded exponential backoff. Do not blindly trust provider-provided values beyond task deadline or configured cap. Stop after bounded attempts; surface retryable status and retain no incomplete source as sealed.
- Limit concurrency per workspace and provider app; queue work rather than fan out requests. Prefer serial/bounded provider requests to reduce secondary limits.
- Idempotency binds workspace, idempotency key, normalized locator, requested ref, and policy version. Duplicate requests reconcile to the existing operation; mismatched reuse conflicts. Each worker stage has durable state/attempt metadata. A reconciler handles stuck stages, removes abandoned staging objects, and never guesses completed state.
- Temporary private archive redirect URLs are short-lived credentials: never persist, log, trace, or return them. If transfer expires, obtain a new URL after rechecking grant.

## 8. Observability and security

Audit actor, workspace, repository/snapshot IDs, requested/resolved ref, permission decision, lifecycle state, outcome, safe error category, sizes/counts, digests, provider request ID, and correlation ID. Exclude token data, authorization headers, temporary archive URLs, raw GitHub response bodies, source content, and unredacted secret-bearing paths. Store path/exclusion detail only in access-controlled manifest. Logs must not turn caller-controlled refs/paths into unescaped log content.

Enforce no credentials in agent context, LangGraph checkpoints, prompts, frontend responses, or API responses. Agent capability resolves a workspace/execution-bound snapshot, validates path against manifest, caps bytes/range, rechecks membership/grant, verifies integrity, and returns content + digest/provenance. No raw storage handle or GitHub client. All ingestion data is untrusted; prompts do not enforce access policy. Sandbox remains the only execution path for repository code, and ingestion never invokes it.

## 9. Test/rollout order and open decisions

Implementation order: (1) finalize GitHub App registration permissions, callback and redirect host policy; (2) define requested-ref contract and migration for current Django submission model; (3) implement private grant broker only after OAuth/security review; (4) finalize inclusion/filtering policy and measured operational limits; (5) select production private object storage, encryption, backup and reconciliation operations; (6) expose repository v1 APIs with workspace authorization/rate limiting and asynchronous orchestration; (7) bind future executions to sealed snapshots and add the Agent Foundation read capability; (8) staging deployment and measured operational limits before production rollout. Public acquisition, safe extraction, manifest integrity, and the local storage/sealing lifecycle are implemented foundations for those steps.

Required tests: public works without credentials; private provider calls blocked before verified exact-repo Contents: read; wrong workspace/repository grant denied; write/PR permissions absent from ingestion token; branch/tag moves after resolution but archive remains SHA-pinned; annotated tags, tag cycles/non-commit refs, full SHA validity; redirects to disallowed hosts and credential stripping; archive traversal, links, bombs, malformed/truncated streams, limits, duplicate names and case collisions; interrupted upload/extraction/digest failure never seals; cancellation and 429/403/5xx policies; private content never appears in logs/API/prompts; all agents bound to same snapshot; workspace ACL and revocation at every read; migration and idempotency behavior.

Open decisions: actual GitHub App registration and exact permissions/capability requirements for repository metadata/ref/archive APIs; supported GitHub.com only or GitHub Enterprise hosts; final ref grammar and annotated-tag handling; archive redirect host allowlist maintenance and safe credential stripping; archive-to-complete-Git-tree integrity/completeness semantics, including submodules/LFS/symlink metadata; source inclusion/exclusion and secrets policy; numeric size/time/concurrency quotas and measured rate-limit/retry behavior; object-store and KMS provider; production task queue/outbox and reconciler runtime; user authorization revocation policy and event delivery; provider API-version review cadence. No paid service, storage vendor, job runner, SDK, or reliability target is selected here.

## 10. Provider references

- GitHub: [Downloading source code archives](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives) â€” archives represent a commit snapshot, not repository history, and compressed bytes can vary.
- GitHub: [Git LFS objects in archives](https://docs.github.com/en/repositories/working-with-files/managing-large-files/managing-git-lfs-objects-in-archives-of-your-repository) â€” LFS payloads are excluded by default (pointer files only); external LFS objects are not included.
- Git: [`git archive` attributes](https://git-scm.com/docs/git-archive#ATTRIBUTES) â€” `export-ignore` can exclude tracked paths and `export-subst` can change archived file contents.
- GitHub Actions: [Runner network requirements](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) list `codeload.github.com` for downloads. One anonymous observation of the REST archive endpoint also redirected to that host; it is enforced as an exact host allowlist and must be rechecked if GitHub behavior changes.

- GitHub REST API: [Download repository archive](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-tar) — archive endpoint returns a redirect; private archive links expire after five minutes; public archive use does not require authentication; Contents: read is required for private.
- GitHub REST API: [Get a reference](https://docs.github.com/en/rest/git/refs#get-a-reference) — branch/tag refs can be resolved and public resources can be read anonymously.
- GitHub Apps: [Generate an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app) — token may be restricted to repositories and a subset of app permissions; defaults to all installation-granted permissions if not restricted; expires after one hour.
- GitHub Apps: [Generate a user access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app) — effective access is intersection of user and app access, tokens are revocable/expiring.
- GitHub REST: [Rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) and [best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api) — honor `Retry-After` and `X-RateLimit-Reset`; use bounded backoff for secondary limits.
