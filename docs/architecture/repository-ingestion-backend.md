# Backend Repository Access, Ingestion, and Snapshot Architecture

**Status:** Proposed end-to-end design with the Django repository domain foundation, anonymous GitHub metadata/ref provider, temporary public archive acquisition, bounded disposable extraction, GitHub v1 manifest/integrity foundation, provider-independent local snapshot storage/sealing service, Agent Foundation, and deterministic Agent 1 structure analyzer implemented. Submission HTTP workflows, GitHub App/private authorization, source inclusion/filtering policy, production object storage, and end-to-end execution scheduling remain unimplemented. This document supplements [repository-workflow.md](repository-workflow.md), [Agent Foundation](agent-foundation.md), [Structure Analyzer](agents/01-structure-analyzer.md), and the ownership rules in [../architecture.md](../architecture.md). Where this document adds operational detail, the earlier workflow requirements remain intact. No accepted decision is withdrawn.

## 1. Scope and invariants

Design the backend path from a submitted GitHub URL to a sealed, immutable repository snapshot and an authorized read interface for later agent execution. Use Django and Django REST Framework with `/api/v1/` versioning. The current implementation includes repository-domain persistence and services, but this end-to-end path is still a design: the backend currently exposes only its health endpoint and has no repository API or provider/ingestion workflow.

### Implemented repository-domain foundation

`backend/apps/repositories/` currently implements the custom `User` model; workspace and membership records with membership/last-owner services; workspace-scoped repository, submission, grant, and snapshot metadata records; audit events; immutable execution-to-snapshot binding records; strict Pydantic service contracts; and domain services for workspace membership checks, record creation/listing, and sealed-snapshot/grant authorization checks. Django migrations persist this schema. These are domain and metadata capabilities: they do not fetch GitHub data, ingest or hash archive files, create a physical snapshot, expose repository API endpoints, or start agent execution. Model metadata and authorization checks alone do not prove that the corresponding external workflow has run.

The GitHub provider/ref/archive acquisition foundation, bounded disposable extraction, and provider-specific versioned manifest/integrity foundation are implemented in the isolated `providers/github/` package. Acquisition stages a SHA-pinned public archive; extraction is a separate context-managed operation on that staged archive and does not perform HTTP. `ArchiveExtractionLimits` defaults are 1 GiB extracted file bytes total, 64 MiB per file, 20,000 members, 1,024 UTF-8 bytes per path, depth 64, 2 GiB decompressed archive bytes, and bounded PAX metadata (64 KiB per member / 1 MiB total). The extractor preflights declared sizes and member types, enforces actual streamed byte counts, writes only regular files/directories, and reads files back to produce actual size/SHA-256 inventory values. The `repository-manifest.v1` contract binds that inventory to GitHub identity, resolved commit SHA, exact staged archive SHA-256, inventory totals, and `source_completeness="unverified"`. Canonical JSON uses UTF-8, NFC paths, sorted keys, compact separators, direct non-ASCII output, and no timestamps; `manifest_sha256` hashes the canonical payload excluding itself. Verification checks manifest digest and, while extraction is active, re-enumerates files without following links and recomputes actual sizes and hashes. Archive, file, and manifest digests cover different bytes and are not interchangeable. Links, devices, FIFOs, sparse and unknown types are rejected. Temporary extraction is deleted on failure and context exit; Windows inherits its temporary parent ACL and does not receive the same explicit POSIX directory-descriptor/no-follow guarantees. These are safety limits for temporary processing, not measured production quotas. Still unimplemented are GitHub App/private authorization, end-to-end submission orchestration, cross-provider inclusion/filtering policy, repository submission/status HTTP endpoints, and orchestration/Agent Foundation execution and content-read integration. Local private snapshot storage and sealing are implemented as described below. Preserve these boundaries when implementing the remaining steps.

Invariants:

- Public repositories are read anonymously. GitHub login is not required for analysis. Any of the eight agents can participate according to mandatory Agents 1–2 and the selected optional groups from `repository-workflow.md`. Agent 6 patch generation, Agent 7 testing, and Agent 8 review do not require GitHub write authorization; testing is separately mediated by `sandbox/`.
- Private repositories require verified repository-scoped read authorization before source retrieval and again before execution begins. A sealed, validated snapshot is required before any agent starts.
- One execution pins one snapshot ID and full Git commit SHA for all selected agents. Refresh creates another snapshot; it never mutates an existing one.
- Ingestion treats repository data as hostile and never executes it. No credentials are included in agent context, prompts, results, or logs.
- Read, content-write, and pull-request permissions are distinct. Publishing is a later, explicit workflow owned by a separate publishing service as specified in `repository-workflow.md`.
- Fail closed when identity, authorization, snapshot integrity, or policy cannot be established. Never report repository visibility, authorization, ingestion, or execution as verified unless the responsible backend step actually succeeded.

## 2. Proposed package/module structure

This is a logical layout beneath the existing roots, not a demand to create every package or an app for every concern. Keep one cohesive Django capability app (suggested `backend/apps/repositories/`) and pure service modules; do not create a second GitHub service system under `repository_engine/`.

```text
backend/
  apps/repositories/
    apps.py
    models/                 # ORM persistence adapters/entities
    migrations/             # Django-owned schema evolution
    api/v1/                 # DRF views, serializers, permissions, urls
    services/               # submit, verify-access, resolve-commit, ingest, snapshot
    tasks/                  # thin background-job entrypoints; chosen job tech deferred
    repositories/           # persistence interfaces/adapters
    tests/                  # API, service, integration and security tests
  domain_services/          # application workflow coordination only
  authentication/           # user identity and future GitHub grant integration
repository_engine/
  contracts/                # source, repository, snapshot, manifest protocols
  github/                   # provider adapter interfaces/implementation boundary
  acquisition/              # URL normalization, identity/access and commit resolution
  ingestion/                # safe retrieval, path policy, manifest and size limits
  snapshots/                # immutable storage and digest validation
  errors.py                 # provider-neutral typed outcomes
shared/contracts/           # only deliberate stable contracts crossing subsystems
```

`backend/apps/repositories` owns Django ORM, transactions, DRF transport, user ownership, and task scheduling adapters. `repository_engine/` owns provider-neutral acquisition and snapshot domain logic, storage interfaces, and GitHub provider adapter boundary; its concrete persistence is injected. The production GitHub adapter must not be imported by agents. `backend/domain_services` coordinates a submission into authorization check, retrieval, snapshot seal, then future orchestration request. It does not own LangGraph state. `backend/agent_foundation/` owns execution contracts and controls; its `SnapshotRepositoryReader` delegates to authorized sealed-snapshot services. `backend/agents/01_structure_analyzer/` is the first deterministic registered agent and receives only the scoped reader interface. `orchestration/` will own execution graph, selected-agent routing, and checkpoints. Publishing remains outside the ingestion service.

Use the selected Django/DRF versions and Pydantic v2 for strict service-boundary validation. Pydantic is not an ORM or a duplicate representation of Django persistence: use it for provider/service inputs and outputs and the immutable snapshot manifest contract, while Django models remain the persistence authority. Pin Pydantic exactly to `2.13.5`, verified importable in the established Python 3.11 environment; do not add a GitHub SDK, object-storage client, or job runner in this design. DRF serializers validate HTTP syntax/shape and map to domain contracts and ORM records.

### Workspace identity and authorization boundary

Use a custom Django `User` based on `AbstractUser`, configured as `AUTH_USER_MODEL` before the first production migration. A `Workspace` is the security and retention boundary. `WorkspaceMembership` joins user and workspace with exactly one of `owner`, `admin`, or `member`; enforce uniqueness on `(workspace, user)`. A workspace must have at least one owner. Owner/admin can manage membership and workspace settings; members may submit repositories and read workspace resources according to explicit feature permissions. Membership must be checked on every submission, grant, snapshot, audit, and content-read operation; random opaque IDs are not authorization.

`RepositorySubmission` has a required workspace FK and submitting-user FK. `RepositoryGrant` has required workspace and repository FKs and records which workspace grant applies to; any user-specific initiating/consenting actor is a separate nullable actor FK and does not broaden workspace access. Enforce that the grant's repository and all submissions/snapshots belong to the same workspace through service validation and, where feasible, database constraints. Repository identity may be shared as provider metadata, but private source bytes and snapshot references are workspace scoped. Cross-workspace deduplication of private content is prohibited unless a separately reviewed authorization design establishes safe tenant isolation; default to separate snapshots/storage namespaces.

Repository ownership and authorization are distinct: workspace membership allows use of workspace-owned CodeWise records, while a `RepositoryGrant` establishes the workspace's verified GitHub read access to a specific private repository. Membership alone never grants GitHub access. No individual agent receives either grant credentials or workspace authorization tokens.

## 3. Service boundaries and flow

### Boundary owners

| Capability | Owner | Contract |
|---|---|---|
| URL submission/API | DRF repository API | `RepositorySubmissionRequest` and sanitized submission/access response |
| User/tenant access and grant lookup | backend authorization service | principal + repository + required permission -> authorized/required/denied; never a token to callers |
| GitHub API calls | `backend/apps/repositories/providers/github/` isolated adapter (anonymous metadata/ref foundation implemented; credential broker deferred) | typed provider request; returns provider data/outcome, not agent capabilities |
| Metadata/commit resolution | repository acquisition service | canonical identity, visibility, branch ref, immutable full commit SHA |
| File transfer/filtering | ingestion service | bounded source entries and safe manifest; no execution |
| Snapshot storage/sealing | snapshot service + injected private storage adapter | immutable snapshot reference and verified digests |
| Persistence/HTTP transaction | Django repositories + DRF | owned ORM state and HTTP mapping |
| Future agent read | authorized snapshot content service | scoped snapshot ID + bounded path/range request -> bytes/metadata, no GitHub credentials |
| Future execution scheduling | orchestration adapter | sealed snapshot ref + resolved stable agent IDs and group IDs |

### Public flow

1. Normalize and validate supported HTTPS GitHub URL forms. Do not follow arbitrary URL redirects, userinfo, alternate hosts, local addresses, query-controlled endpoints, or repository paths beyond `owner/name` (optionally strip one terminal `.git`). Reject non-GitHub hosts and unsupported URL forms. Do not fetch at URL-validation time.
2. Resolve public repository metadata anonymously using bounded provider calls. A 404 can mean absent or inaccessible; return privacy-safe errors. Confirm provider canonical identity and public visibility. Resolve selected/default branch to a full commit SHA; a branch name is not a snapshot identity.
3. Create an idempotent submission record, then enqueue retrieval. Response reports `resolving`/`retrieving` and does not claim verification prematurely.
4. Retrieve a commit-pinned archive or equivalent source representation. Archive retrieval must target the resolved commit SHA, not a mutable branch. Enforce response, decompression, file-count, per-file, aggregate-size, and time limits while streaming. Reject unsafe paths and archive entries.
5. Produce a manifest and content digests, seal immutable snapshot storage, and record provenance. A later execution may start only after sealed status and integrity verification.

### Private flow

1. URL normalization can be local and does not assert repository existence or visibility. When anonymous resolution cannot establish accessible public metadata, return an authorization-required state without disclosing private existence.
2. Authorization is a separate future GitHub App integration, not implemented now. The authorization service must verify that the authenticated principal has repository-scoped contents-read permission for the exact canonical repository identity. Do not retrieve source before that check.
3. Only after verified access, retrieve metadata and commit with the scoped read grant. Never broaden the grant to write or PR permissions. Bind retrieval to a full SHA and record a grant reference/version for audit, not the token.
4. Seal and verify a private snapshot in a user/tenant-isolated storage namespace. Before execution, revalidate that the principal/execution remains entitled to use the private snapshot; revoked/expired authorization blocks execution and future reads. Policy for already-running work on revocation must be explicitly defined and default to cancellation/denial at the next safe boundary.

Private URL handling cannot prove private status without authorized metadata. The API should say “access could not be verified; connect an account to request repository access,” not “this is definitely private.”

## 4. Contracts and data model

All domain contracts are explicitly versioned (e.g. `repository.submission.v1`, `repository.snapshot-ref.v1`, `repository.file-entry.v1`). Python examples below are conceptual strict schemas. Unknown fields should be rejected at external trust boundaries unless a versioned compatibility policy explicitly permits them. IDs are opaque; never derive agent IDs from directory names.

### Domain contract shapes

```text
RepositoryLocatorV1 { provider: "github", url: str }
RepositoryIdentityV1 { provider: "github", provider_repository_id: str,
  owner: str, name: str, canonical_url: HTTPS URL }
RepositoryMetadataV1 { identity: RepositoryIdentityV1,
  visibility: public|private|internal|unknown, default_branch: str|null,
  resolved_ref: str, commit_sha: full_hex_sha, observed_at: datetime }
RepositoryAccessV1 { status: not_required_public|required|pending|verified_read|
  denied|revoked|expired, permission_set: [read], checked_at: datetime|null,
  grant_ref: opaque|null }
SnapshotRefV1 { snapshot_id: opaque, repository_id: opaque,
  commit_sha: full_hex_sha, visibility: enum, manifest_digest: sha256,
  status: sealed, created_at: datetime }
```

### Django entities (proposed)

- **Repository**: internal UUID, provider, provider repository ID (unique per provider), owner/name and canonical URL, last-known visibility, default branch, created/updated timestamps. Canonical provider ID is identity; owner/name can change and URL is not identity. Prevent duplicate records across case/URL variants.
- **User**: custom `AbstractUser` configured as `AUTH_USER_MODEL` before initial repository/user migrations; retain Django's standard authentication semantics and avoid a second credentials model.
- **Workspace**: UUID, name, configurable bounded private-source retention days (default 30), created/updated timestamps. Workspace is the tenant/security boundary for repository records and private snapshots.
- **WorkspaceMembership**: workspace FK, user FK, role (`owner`, `admin`, `member`), timestamps; unique `(workspace, user)`, indexed by `(user, role)` and `(workspace, role)`. Enforce at least one owner in transactional membership services; ordinary row checks cannot reliably enforce cross-row owner existence.
- **RepositoryGrant**: internal ID, workspace FK, repository FK, grant type/provider installation reference, protected credential reference (never token value in ordinary DB), permission snapshot, status, granted/checked/expiry/revoked timestamps, provider account/installation IDs, optional actor FK. Unique scope constraints prevent ambiguous active grants. Public access has no grant row requirement.
- **RepositorySubmission**: UUID, required workspace FK, submitting-user FK, submitted URL (sanitized; avoid retaining query/userinfo), repository FK nullable until resolved, normalized locator, status, error code, idempotency key, request fingerprint, timestamps. Unique `(workspace, idempotency_key)` and prohibit a reused key with a different fingerprint.
- **RepositorySnapshot**: UUID, required workspace FK and repository FK, commit SHA, visibility-at-capture, source grant reference nullable for public, status (`resolving`, `retrieving`, `validating`, `sealed`, `failed`, `expired`, `deleting`, `deleted`), manifest schema version, manifest digest and private manifest object reference, content digest, storage namespace/key reference, byte/file counts, provenance, created/sealed/last-analysis-completed/expiry timestamps, failure category. Refresh always creates a new snapshot ID. A content-equivalence cache must never cross workspace boundaries.
- The complete file list and exclusions live in the versioned immutable manifest object; database stores manifest schema/digest/reference and aggregate counts. Do not duplicate every manifest file as ORM rows without a measured query requirement and separate decision.
- **AuditEvent**: append-only actor/tenant, action, repository/submission/snapshot/grant references, correlation ID, timestamp, outcome, safe reason code, non-secret provider request ID. Never source bodies, tokens, authorization headers, or raw provider payloads.
- **ExecutionSnapshotBinding** (owned by future orchestration/execution domain, not ingestion): execution ID, snapshot ID, commit SHA, resolved stable agent IDs/group IDs, pinned contract version. All selected agents read the same binding; foreign key/immutable write-once rule prevents snapshot substitution.

Database transactions coordinate local state changes and outbox/task enqueue (transactional outbox is preferred if task queue delivery must survive process crashes). They cannot make remote GitHub calls atomic; each remote stage is an explicit state transition with idempotent reconciliation.

### Snapshot integrity and source storage

Implemented under `backend/apps/repositories/storage/` is the provider-independent `SnapshotStorage` protocol and a local filesystem adapter. The adapter uses a configured absolute root outside Django static/media roots, opaque workspace/snapshot UUID references, a private `.staging` area and this layout: `workspaces/<workspace UUID>/snapshots/<snapshot UUID>/{manifest.json,files/<repository path>}`. It does not expose filesystem paths or public URLs. The GitHub adapter converts the existing `repository-manifest.v1` into provider-neutral file records; it does not introduce a competing manifest schema.

The lifecycle service checks workspace membership and, for private/internal snapshots, a current verified `contents_read` grant. It verifies the active extraction, repository identity, full commit SHA, archive digest linkage, canonical manifest digest and `source_completeness="unverified"`; copies only manifest-listed regular files; verifies the staged manifest, exact file set, sizes and SHA-256 values; atomically renames the staged directory on the same filesystem; re-verifies published bytes; then records references/counts and seals the snapshot in a database transaction with audit events. Reads require an authorized sealed snapshot and use controlled manifest/file/inventory/integrity service methods. Agents receive no filesystem path or storage credential. No snapshot is exposed as usable before sealing.

Snapshot model saves reject identity changes, illegal lifecycle transitions, direct initial sealing and sealed content metadata changes. QuerySet bulk update/delete and hard deletion are blocked through the model manager. These are application-level safeguards: privileged SQL or a host administrator can bypass them or alter files. POSIX roots/directories are mode 0700 and files 0600, with no-follow and descriptor-relative source opens where supported. Windows inherits filesystem ACLs and uses path-based checks; it does not provide equivalent custom ACL, race, or no-follow guarantees. Configure the private root outside any web server's document roots and protect it through host access controls. Disk encryption at rest is a deployment responsibility.

Filesystem publication and the database transaction are not one atomic transaction. A crash before rename leaves only staging data; the cleanup method removes old staging directories. A crash after rename but before database sealing leaves an inaccessible final directory and an unsealed/failed row; `recover_published_snapshot` re-parses the canonical manifest, verifies its metadata and every stored file, and seals only on success. Retrying publication for the same UUID reuses data only when the complete manifest and file inventory match; mismatches collide and are never overwritten. This supports reconciliation but is not full database/filesystem atomicity. A final directory left without a usable database record requires operational reconciliation; no automatic destructive orphan purge is claimed.

The canonical persisted object remains the existing `repository-manifest.v1` (including repository identity, commit, archive digest, file hashes/counts, and `source_completeness="unverified"`). No timestamp, exclusions, or new snapshot manifest format is added. File SHA-256 covers extracted bytes; archive SHA-256 covers downloaded archive bytes; manifest SHA-256 covers canonical manifest payload excluding its digest field. The separate snapshot `content_digest` uses a versioned encoding of sorted paths, byte lengths, and file digests. Verification checks each distinct layer. None proves complete or faithful Git-tree retrieval.

### Retention, deletion, and recovery

Default private-source retention is 30 days after the last completed analysis using the snapshot. The service refreshes expiry when analysis completion is recorded; when no analysis completion exists, sealing time is the fallback anchor. Workspace retention is bounded to 1–3650 days. Existing execution bindings have no completion state, so the implementation conservatively blocks expiry/deletion whenever any binding exists; completed executions may therefore retain snapshots until an execution lifecycle supplies completion state. Expiry/deletion services retain minimal audit records.

Deletion is a state transition (`sealed -> expired -> deleting -> deleted`), idempotent and retryable. Reads reject non-sealed snapshots. The local adapter removes the private snapshot directory; failures leave the row deleting for retry. The service records initiation/completion/failure audit events and does not delete the database snapshot row. Production object storage, backup purge schedules, and operational reconciliation remain future work.

## 5. Safe file acquisition and filtering

The implemented GitHub extraction and manifest foundation is intentionally narrower than this proposed ingestion pipeline. Extraction returns a temporary, path-sorted inventory containing each normalized relative path, actual byte count and SHA-256 of bytes read back from the extracted file, along with file count, total bytes and `source_completeness="unverified"`. The `repository-manifest.v1` Pydantic contract captures GitHub repository identity, full commit SHA, exact archive SHA-256, file records, counts, completeness state, canonicalization version and manifest digest. Canonical bytes use UTF-8 JSON with sorted keys, compact separators, direct Unicode output and NFC-normalized paths; the digest excludes its own field and excludes timestamps (none are included). The verifier validates manifest structure and digest, checks optional resolved repository/commit and staged-archive linkage, and can compare an active extracted tree’s path set, regular-file types, sizes and recomputed file hashes. Manifest input is capped at 32 MiB and 20,000 records. These digests verify bytes and linkage only; they do not compare the archive to Git tree objects or establish that every repository file is present. GitHub archive behavior may omit submodule content, supply Git LFS pointer files without LFS payloads, or reflect `export-ignore` / `export-subst` and generated/excluded content semantics. Commit SHA pinning alone does not prove source completeness. The extraction context removes its temporary directory on success exit and failure; POSIX systems with `dir_fd` support use descriptor-relative traversal and no-follow flags, with root mode 0700 and output files 0600. Windows uses path-based checks and exclusive creation, without equivalent custom ACL or no-follow race protections; its effective access partly depends on the temporary parent ACL. No archive, extracted file, or manifest is made permanent or sealed.

- Pin archive/download to resolved commit SHA and verify provider metadata agrees. Use HTTPS with fixed GitHub API/archive hosts and validated redirects limited to an explicit host allowlist; disable arbitrary proxy behavior and reject DNS/IP destinations outside policy if any URL fetch capability exists.
- Stream with connect/read/overall deadlines, bounded redirects, compressed and decompressed byte caps, file count and per-file limits, maximum path length/depth, and cancellation. Provider rate-limit headers are parsed as data, not blindly trusted for arbitrary sleep intervals.
- Normalize separators to `/`; reject absolute paths, drive prefixes, UNC paths, NUL/control characters, `.`/`..` traversal, duplicate normalized paths, case-collision paths where the target storage is case-insensitive, symlinks/hardlinks, device/special files, and archive entries escaping extraction root. Do not follow symlinks. Reject or safely skip with recorded reason according to a versioned ingestion policy; an incomplete repository must never masquerade as a complete snapshot. Prefer fail the snapshot for unsafe/unsupported archive semantics.
- Filter VCS internals and generated/oversized/binary files by explicit versioned policy; keep a manifest disposition/reason and distinguish “excluded by policy” from “missing.” Define inclusion rules before implementation because agent completeness depends on them. Never honor repo-supplied ignore/build scripts to execute or dynamically choose policy.
- Store raw bytes and bounded text decoding metadata separately. Agent content reads are bounded by snapshot, path allowlist/manifest, byte/range limits, caller authorization and execution scope. Files are data; prompt-injection-like instructions are untrusted. Never recursively expand archive formats or parse with unsafe deserializers during ingestion.
- Never install dependencies, run tests, hooks, build files, package managers, git filters, or repository scripts in the acquisition worker. Controlled test/build execution occurs only through `sandbox/` after explicit platform policy authorization.

## 6. Proposed API contracts (`/api/v1/`)

These are proposals only, not implemented endpoints. API uses DRF authentication for CodeWise users where account context exists; public repository content retrieval does not require a GitHub login. Protect object endpoints with owner/tenant permissions. Apply size/content-type validation, rate limits and correlation IDs at ingress.

### Submit or resolve a repository

`POST /api/v1/repositories/submissions/`

Request:

```json
{ "url": "https://github.com/owner/repository", "idempotency_key": "opaque-client-key" }
```

Response `202 Accepted`:

```json
{ "submission_id": "sub_…", "status": "resolving", "repository": null,
  "access": { "status": "unknown" }, "snapshot": null,
  "next_action": "poll_submission", "correlation_id": "…" }
```

Fields: URL must be HTTPS GitHub canonical web URL with exactly owner/repository path (one trailing slash and optional `.git` may be normalized); reject credentials, port overrides, fragments, nonstandard hosts, GitHub Pages/repository subpaths, tree/blob URLs, search/query strings, SSH/scp forms unless explicitly added in a versioned contract. Allow syntactically valid public/private locators without implying they exist. Return `400 invalid_repository_url` with safe field errors; `409 idempotency_conflict` for key reused with different normalized request. Rate limit per principal and source IP. Whether this API synchronously resolves public repositories or only creates a submission is deferred; preferred `202` avoids unbounded provider I/O in request threads.

### Poll submission/access/snapshot state

`GET /api/v1/repositories/submissions/{submission_id}/`

Response `200`:

```json
{ "submission_id": "sub_…", "status": "awaiting_authorization",
  "repository": { "repository_id": "repo_…", "owner": "owner", "name": "repository",
    "canonical_url": "https://github.com/owner/repository", "visibility": "unknown" },
  "access": { "status": "required", "permissions": ["contents_read"], "checked_at": null },
  "snapshot": null, "error": null }
```

Possible states: `resolving`, `awaiting_authorization`, `retrieving`, `validating`, `sealed`, `failed`, `cancelled`. Only verified public metadata may return `visibility: public`; private existence is not disclosed until authorized. Polling is owner-scoped and rate-limited. `POST /api/v1/repositories/submissions/{id}/access-verification/` can be proposed for after a future authorization connection exists; request `{ "required_permissions": ["contents_read"] }`, response gives scoped authorization status only. It does not accept credentials or imply OAuth implementation now.

### Start a future execution

Execution creation is described by `repository-workflow.md`; implementation must enforce `snapshot.status == sealed`, revalidate private authorization, pin snapshot ID/SHA and derive agent IDs from stable server registry + selected stable group IDs. Reject unknown groups and individual optional agent IDs. This ingestion design adds no endpoint or fake execution behavior.

### Error envelope and mapping

```json
{ "error": { "code": "repository_access_required", "message": "Repository access must be authorized before source can be retrieved.", "retryable": false, "field_errors": {}, "correlation_id": "…" } }
```

Safe codes: `invalid_repository_url`, `unsupported_provider`, `repository_not_accessible`, `repository_access_required`, `repository_access_denied`, `authorization_expired`, `authorization_revoked`, `provider_rate_limited`, `provider_unavailable`, `commit_unresolvable`, `snapshot_size_limit_exceeded`, `unsafe_archive_entry`, `snapshot_integrity_failed`, `submission_not_found`, `idempotency_conflict`, `temporarily_unavailable`. Use `404` or privacy-preserving generic inaccessible responses where private existence could leak; do not rely on status code alone to prevent timing leaks. Map invalid input to 400, unauthenticated platform user to 401, forbidden owned-resource access to 403/404 policy, stale state to 409, rate limit to 429 with bounded `Retry-After`, upstream unavailable to 502/503, and unknown safe internal failure to 500. Never return raw GitHub messages, source, filesystem paths, tokens, headers, stack traces, storage keys, or private repo names in unentitled responses.

## 7. Authorization and credential lifecycle

OAuth is explicitly out of scope. Before adding it, choose GitHub App installation/user authorization mechanics and permission names against current GitHub documentation. In the future:

- Anonymous public reads use no user credential. Private read grants are repository-scoped and contents-read only. Write and pull-request grants belong to the separate publishing service, requested only at publish time and after human approval.
- Token broker resolves short-lived access tokens from encrypted secret storage only inside provider adapters. Prefer managed secret/KMS envelope encryption; encrypted DB fields are fallback only with key rotation and separation. Application DB stores a non-secret secret reference and scope metadata. Never persist plaintext tokens in settings, job payloads, task arguments, exceptions, telemetry, or audit.
- Authorization records bind platform user/tenant, GitHub account/installation, repository IDs, scopes/permissions, expiry and status. Verify effective permission at each private retrieval and execution/read boundary, not merely at connection time. No permission inheritance from connected account to all repositories.
- Revocation/expiry events and periodic checks mark grants invalid; recheck at operation boundaries, fail closed, prevent queued work from starting, cancel active work at safe checkpoints, deny future snapshot reads, and apply retention/deletion policy to private snapshots/indexes. Product policy for user-owned shared snapshots after disconnect must be finalized; default to no new access and eventual deletion when no authorized owner remains.
- Audit grant creation/change/revocation, verification, source access, snapshot lifecycle, denied access, and execution binding. Append-only/event-integrity controls are required; logs are metadata only.

## 8. Reliability, limits, idempotency, and recovery

Set explicit configurable budgets before launch; numeric values require repository-size measurements and provider/deployment limits, not assumed universal defaults. Independently bound HTTP request time, provider connect/read timeout, overall ingestion deadline, archive compressed/decompressed bytes, entry count, file size, aggregate extracted bytes, path depth/length, retries, queue age, and concurrent ingestions per user/repository/worker. Use cancellation and cooperative checkpoints.

Retry only transient network errors, `429`, and selected `5xx` responses with bounded exponential backoff plus jitter, honoring valid provider reset times within the overall deadline. Do not retry invalid URLs, permission denials, unsafe archives, integrity mismatch, or other deterministic failures. Reads are side-effect-safe but still rate-limited; use conditional requests/ETags only when pinned metadata semantics are correct. Persist attempt count and provider request IDs without sensitive payloads.

Use `(principal, idempotency_key, normalized_request_fingerprint)` for submissions. Duplicate accepted requests return the same submission; differing payload under same key conflicts. Worker steps are resumable via explicit states and operation keys. Do not mark a snapshot sealed until storage and all integrity checks complete. On transient failure, retry stage under deadline; on permanent failure, mark terminal and clean staging objects. A periodic reconciler detects stuck states and incomplete staging but must not guess success. Garbage collection deletes only expired, unreferenced staging/snapshot data according to retention and audit policy. Deletion is idempotent.

Provider outage, rate limits, revoked permissions, commit movement/deletion, oversized repositories, invalid archive paths, storage outage, digest mismatch, worker crash, database/task-queue split failure, duplicate delivery, and authorization changes mid-ingestion each have typed state/error mapping. Keep truthful state; partial content is quarantined and never supplied as a complete snapshot. Do not make an uptime or reliability percentage claim without measured service objectives and operational evidence.

## 9. Agent 1 Structure Analyzer integration

Agent 1 is implemented at `backend/agents/01_structure_analyzer/` and registers stable ID `structure_analyzer` through the Agent Foundation registry. Its current strict input binds execution/workspace/repository/snapshot IDs, commit SHA, manifest digest, and bounded analysis options. The current output contracts and detection limits are documented in [Structure Analyzer](agents/01-structure-analyzer.md). A future orchestrator will invoke it through the public lifecycle interface; no API or workflow scheduling integration is implemented yet.

The intended future orchestration payload shape is:

```text
StructureAnalysisInputV1 {
  execution_id, snapshot: SnapshotRefV1, repository: RepositoryIdentityV1,
  commit_sha, analysis_policy_version, allowed_file_categories,
  content_capability_ref, deadline, cancellation_ref, correlation_id
}
```

Pass IDs/references and scoped capabilities, not a whole repository, provider client, credential, unrestricted filesystem, or raw storage key. Agent 1 calls a read-only `SnapshotContentReader` capability (`list_files(snapshot_id, bounded_filter, page_token)`, `read_file(snapshot_id, normalized_path, max_bytes)`), authorized by execution binding and snapshot ACL. Return bytes plus path, length, digest, and provenance; enforce limits and integrity in the platform reader. File content remains untrusted data. Agent 1 performs deterministic inventory/structure analysis and returns a versioned result with `snapshot_id`, full commit SHA, analysis policy version, evidence refs (path + digest + bounded line/byte span), and explicit completeness/omission status. It does not fetch GitHub, resolve credentials, execute files, or invoke an LLM. Foundation/orchestrator verifies result snapshot and commit exactly match execution binding; mismatch fails the node. Future agents use the same snapshot binding and capability model, while selected group mapping remains solely in `repository-workflow.md`.

Cancellation, timeout, authorization revocation, corrupted content, and file-read policy errors are typed outcomes. A partial inventory must be marked partial with omission reasons; it cannot become a complete successful result. Large sources are paginated and selectively read rather than copied into LangGraph state/checkpoints. Checkpoint only snapshot IDs, immutable evidence/artifact refs, bounded metadata, and explicit policy/version identifiers—not source bytes, credentials, or file handles.

## 10. Testing strategy for implementation

The existing GitHub provider foundation has mocked tests for public archive redirects, limits, integrity, and temporary cleanup. For future ingestion implementation, also cover:

- URL normalization and rejection: public/private syntax, trailing slash/`.git` normalization, userinfo, fragments/query, alternate hosts, private IP/localhost, traversal, Unicode/control characters, redirects, port/host confusion, unsupported forms.
- Public anonymous retrieval succeeds without credentials; private retrieval cannot call provider source API until exact-repository read access verifies; write and PR scopes are never requested in ingestion.
- Commit resolution pins full SHA despite branch movement; archive request matches SHA; all selected agents bind one snapshot; refresh produces a new ID.
- Archive traversal, absolute/drive/UNC paths, symlink/hardlink/special files, duplicate/case-collision paths, decompression bombs, huge count/file/path, malformed archive, binary filtering, deterministic manifests.
- Source completeness reporting: submodule gitlinks without fetched submodule content, Git LFS pointer files versus payloads, `.gitattributes` `export-ignore` paths, generated/excluded content, and the rule that a resolved commit SHA does not prove that an archive represents the complete Git tree.
- Digest tamper, missing object, partial upload, crash-before-seal, duplicate delivery, staging cleanup, replay, idempotency-key conflict, and reconciliation behavior.
- Cross-user/tenant private snapshot access, guessed IDs, unauthorized poll/read, revoked/expired grant before retrieval and before execution, and no credential exposure in logs/API/task queues/prompts.
- Provider `429`, `5xx`, timeout, malformed response, rate-limit reset abuse, unavailable storage/database/task queue, cancellation, and retry deadline exhaustion.
- API status/error shape, privacy-preserving private behavior, field validation, rate-limit response, schema version/unknown field rejection, and object-level authorization.
- Agent 1 receives only scoped content capability; output provenance matches snapshot; code paths do not import GitHub SDK/provider adapters, model adapters, or sandbox execution; static architecture import rules remain valid.

Use deterministic fake provider/storage/credential-broker adapters for unit and contract tests. Keep tests for actual GitHub credentials/environment opt-in and isolated; never make CI depend on a live user repository.

## 11. Implementation sequence and dependencies

1. **Confirm remaining policy inputs:** measured repository size limits, supported GitHub URL forms, inclusion/exclusion policy, workspace onboarding/last-owner behavior, and environment/deployment storage constraints. Workspace ownership, 30-day default private retention, storage abstraction, manifest shape, and Pydantic v2 are now decisions in this document.
2. **Define versioned contracts and error taxonomy:** shared only if cross-subsystem; keep API serializers/backend ORM mappings distinct. Review privacy disclosure behavior.
3. **Complete production adapter decisions:** the local private storage adapter exists; verify GitHub App permissions and choose production secret manager, private object storage, transactional outbox/task delivery and encryption configuration before adding integrations.
4. **Implement repository identity/access interfaces and public anonymous provider path:** tests prove no auth is used for public access. Add private grant verification only after OAuth/App decisions are approved; until then private source retrieval stays unavailable.
5. **Complete ingestion policy and operational limits:** define inclusion/exclusion policy and measured production quotas. The current snapshot lifecycle persists the provider-level `repository-manifest.v1` unchanged; do not imply completeness or silently introduce a competing manifest schema.
6. **Add repository DRF v1 submission/status API, rate limiting and asynchronous task delivery:** the repository ORM, migrations, domain services, audit records, and execution-binding metadata are already implemented; add object permissions, sanitized responses, and API/task behavior without treating metadata creation as ingestion.
7. **Integrate future orchestration and Agent 1 capability:** only accept a physically sealed snapshot ref, pin snapshot+commit for execution, revalidate private authorization, ensure LangGraph state carries references rather than source.
8. **Threat-model review, adversarial tests, deployment controls and operational measurement:** define service objectives only from measured behavior, alerts, quotas, backup/retention/deletion runbooks before production use.

Dependencies: steps 1–3 precede secure implementation; public read and private read paths share acquisition/snapshot contracts but private path depends on the later authorization decision. Snapshot ingestion can be tested with fake adapters before OAuth. Agent execution depends on sealed snapshot and authorization checks; patch publishing remains a separate future workflow.

## 12. Risks and unresolved decisions

- **Private-repository resolution and privacy:** choosing between generic authorization-required response and richer authorized lookup affects UX and enumeration/timing risk.
- **GitHub App access model:** installation grants vs user authorization, permission names, callback/revocation mechanisms, organization approval, account identity binding, and token lifecycle need validation against current provider behavior before implementation. OAuth is not specified or implemented here.
- **Repository archive semantics:** provider archive endpoints may have redirect/content/size behaviors; choose an API/archive strategy, verify commit binding, and threat-model redirects. Commit pinning does not alone prove archive completeness or integrity; define verification and represent omissions.
- **Snapshot operations:** local private filesystem storage and sealing are implemented. Encryption/key management details, legal deletion and backup purge timing, and production object-store implementation remain deployment decisions.
- **Completeness and integrity policy:** archive verification, file exclusions, symlink handling, submodules, Git LFS pointers, generated/vendor files, binary files, and very large repositories affect agent accuracy; record policy version and never silently imply full source coverage. SHA pinning alone is not an integrity or completeness proof.
- **Database scale:** one file row per path may be expensive; choose manifest object vs rows based on measurements while retaining indexed access and integrity guarantees.
- **Async delivery:** job runner, transactional outbox, concurrency and recovery technology are deferred in canonical architecture. Must ensure DB/task queue cannot lose accepted submissions.
- **Pydantic dependency:** `pydantic==2.13.5` is selected and pinned after verification in the established Python 3.11 environment; test the exact lock in CI when implementation begins.
- **Authorization revocation during active execution:** exact cancellation and private snapshot cleanup behavior needs a product/security decision; fail closed at safe boundaries by default.
- **Error and timing side channels:** status codes, response times, retries and repository names must not expose private existence to unentitled users.
- **Operations:** storage quota exhaustion, provider throttling, rate-limit and retry policy, and large archive abuse require measured limits, quotas, alerting and cleanup procedures. Reliability claims require production evidence.
