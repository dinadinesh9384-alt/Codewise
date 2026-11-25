# ADR-002: Commit-Pinned GitHub Source Acquisition

- **Status:** Accepted architectural direction; anonymous metadata/ref, temporary public archive acquisition, and bounded disposable extraction foundations implemented; private authorization and durable ingestion deferred.
- **Decision date:** 2026-11-23
- **Scope:** GitHub repository identity/ref resolution and source archive acquisition only. This does not implement OAuth, ingestion, storage, or publishing.

## Context

Repository analysis requires repeatable source bound to an immutable commit. Public analysis must not require GitHub authentication. Private source requires a verified repository-scoped read grant before metadata/source access. The ingestion worker must treat archives as hostile and agents must not call GitHub or receive credentials.

## Decision

Use GitHub REST endpoints through an isolated provider adapter in the backend repository boundary. Provider-neutral acquisition contracts and archive processing remain owned by `repository_engine/`; the adapter's current implementation lives at `backend/apps/repositories/providers/github/` so the Django backend can use it without adding provider logic to domain services. Resolve a user-supplied branch or tag using the Git References API, resolve annotated tags recursively to a commit, and validate the resulting full SHA. If the input is already a full commit SHA, verify it exists and belongs to the canonical repository. Download the repository tar archive using the REST archive endpoint with that full SHA as `ref`; never download a moving branch after resolution. Public metadata, ref, and archive reads use anonymous requests. Private calls use a short-lived, repository-scoped GitHub App installation token with Contents: read only, issued by the backend credential broker after exact-repository access verification. Ingestion does not request Contents: write or Pull requests: write.

Allow redirects only for the archive endpoint and only after validation of scheme, host allowlist, redirect count, and destination. Do not forward GitHub authorization headers or cookies to an archive/CDN host; use the redirect URL as a short-lived bearer secret for private archive access and redact it from logs. Enforce TLS, response/decompression limits, deadlines, and cancellation.

The REST archive response is a transport container, not a trusted filesystem tree. Extract through the bounded, path-validating ingestion layer. A 302 response is an expected step; an archive download is not complete until the final bounded byte stream and extracted manifest pass integrity checks. If a private archive URL expires during retrieval, reacquire it through the authorized API within bounded retry policy; never expose it to clients or agents.

## Consequences

- Branch and tag names are provenance fields only; snapshot identity is repository ID + full commit SHA + workspace + ingestion policy/schema.
- Public retrieval works without a GitHub account. Private retrieval is blocked until verified read authorization.
- REST and archive behavior is isolated behind replaceable provider interfaces; no SDK is mandated.
- Redirect behavior and archive endpoint semantics must be integration-tested against GitHub without weakening SSRF/credential-forwarding controls.
- GitHub REST API version is a deployment configuration to be pinned and periodically reviewed; it is not embedded into agent contracts.

## Alternatives considered

- **Clone with git:** rejected for initial acquisition because it expands the executable/tooling attack surface and makes credential/helper configuration harder to constrain. Reconsider only with a separate threat-model decision.
- **Download by branch/tag without resolving:** rejected because refs move and analysis would not be reproducible.
- **Unauthenticated public-only flow with OAuth for all repositories:** rejected because it interrupts public analysis and conflates public read with private authorization.
- **Direct content endpoint for every file:** deferred; archive retrieval is one bounded transfer and returns the repository at a specified ref. File-by-file retrieval can be an adapter alternative if measured archive or provider behavior requires it.

## Implementation status and verification limits

The accepted provider-acquisition decisions above remain architectural direction. The isolated `backend/apps/repositories/providers/github/` package performs anonymous repository metadata lookup and branch/tag/commit resolution and can stream a public commit-pinned tarball to temporary staging with size limits, gzip/tar validation, and a SHA-256 digest. It follows a single validated HTTPS redirect to `codeload.github.com`; the implementation attaches no credentials. Its separate extraction module preflights tar members, bounds decompression and extracted files, rejects links and special entries, writes only to disposable temporary staging, and returns a deterministic per-file SHA-256 inventory. It does not implement OAuth/private access, source inclusion/filtering policy, canonical snapshot manifest or source completeness verification, physical snapshot storage/sealing, repository API workflows, or agent execution. Separately, `backend/apps/repositories/` persists workspace-scoped repository, grant, submission, and snapshot metadata and execution-to-snapshot bindings, with domain authorization/record services.

The adapter's physical placement under the Django repository app is an implementation-boundary choice for this backend, not a decision to place provider calls in repository domain services. Provider-neutral acquisition and ingestion contracts remain assigned to `repository_engine/`.

Resolving a full commit SHA and requesting an archive for that SHA pins the requested source version, but SHA pinning alone does not prove archive completeness or faithful representation of the Git tree. The public temporary downloader currently allowlists `codeload.github.com`, follows one validated HTTPS redirect, sends no credentials, checks the gzip stream and tar structure, and computes a compressed-byte digest; extraction computes hashes of retrieved files but does not compare the result to the Git tree. Submodule and Git LFS coverage, generated/excluded content, archive integrity against the complete tree, GitHub App setup, and measured rate-limit/retry/operational limits remain unresolved. These open items do not change ADR-002's accepted choice of commit-pinned REST archive acquisition as the initial design direction.
