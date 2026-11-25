# Repository Workflow, Agent Selection, and GitHub Boundaries

**Status:** Proposed architecture for CodeWise V2; contracts are not implemented. This document supplements `docs/architecture.md`. The eight-agent roster, Agent Foundation, LangChain/LangGraph split, gateway ownership, and sandbox ownership defined there remain authoritative. No V1 behavior or application code is changed.

## 1. User journeys

### Public repository analysis

The submission screen accepts a GitHub repository URL, displays mandatory Agents 1 and 2 plus three optional groups, and offers a Run button. All optional groups are enabled by default. Advanced settings is collapsed behind a small chevron and contains optional GitHub account connection. A public repository can be retrieved and analyzed without GitHub authorization. CodeWise resolves an accessible commit, creates an immutable snapshot, validates the selected group IDs, and starts execution. The selected eligible agents all consume the same snapshot reference. Results and status are returned to the existing CodeWise dashboard.

### Public repository patch proposal and publishing

The user submits a public URL and selects groups. Connecting GitHub in Advanced settings is optional and never blocks analysis. Agents 6, 7, and 8 are available as part of their selected groups: Agent 6 proposes a local patch, Agent 7 tests it through the secure sandbox, and Agent 8 reviews it. CodeWise presents the patch and evidence. No GitHub write action occurs during agent execution. The user must explicitly choose **Publish Patch** and approve the target repository and base branch. Only then does the publishing service obtain/verify write and pull-request permissions, create a separate working branch, apply the reviewed patch, push that branch, and open a pull request. Direct pushes to the base or main branch are prohibited by default.

### Private repository

On an inaccessible private URL, CodeWise reports that repository read authorization is required and initiates authorization for that repository. The access layer verifies the repository identity and effective read permission before retrieving source. After retrieval, it binds a commit SHA and creates a snapshot. Execution is unavailable until this snapshot exists. The user then selects groups and starts execution. A failed authorization, identity mismatch, or inaccessible commit fails closed; no agent receives partial private source.

## 2. Official agent selection contract

There are exactly eight official stable IDs. Directory prefixes are organizational only; IDs come from the trusted registry and must never be parsed from directory names.

| Agent | Stable ID | Selection rule |
|---|---|---|
| 1 | `structure_analyzer` | Mandatory, always runs |
| 2 | `business_logic_analyst` | Mandatory, always runs |
| 3 | `code_quality_analyst` | Group A |
| 4 | `security_analyst` | Group A |
| 5 | `migration_planner` | Group A |
| 6 | `patch_author` | Group B |
| 7 | `test_engineer` | Group B |
| 8 | `review_agent` | Group C |

Optional selection consists only of these groups; each group defaults to selected:

| Stable group ID | Display name | Exact agent IDs |
|---|---|---|
| `code_quality_security_planning` | Code Quality & Security Planning | `code_quality_analyst`, `security_analyst`, `migration_planner` |
| `patch_engineering` | Patch Engineering | `patch_author`, `test_engineer` |
| `final_review` | Final Review | `review_agent` |

The API accepts group IDs, never individual optional-agent choices. The resolved execution roster is always Agents 1 and 2 plus the union of selected groups. Each selected agent remains subject to runtime availability and policy checks. All eight agents are eligible for public repository execution, subject to selection and normal policy; private execution additionally requires verified read access and a snapshot.

## 3. Repository identity, access, and snapshot lifecycle

### Repository and access contract

`RepositoryIdentity` is a provider-qualified immutable identity, preferably GitHub's repository numeric/node ID, plus normalized `owner/name` as a display/lookup locator. URLs are input locators, not canonical identity. `visibility` is `public | private | internal | unknown`; unknown visibility is treated as inaccessible until verified. `RepositoryMetadata` records canonical URL, default branch, current resolved commit SHA, provider identity, visibility, and retrieval time. A branch name is mutable and is never a source version identifier.

`AccessAuthorizationStatus` is one of `not_required_public`, `required`, `pending`, `verified_read`, `verified_write`, `denied`, `revoked`, or `expired`, scoped to user, repository identity, permission set, and expiry/check time. It is a status record, not a bearer credential. Public unauthenticated read is sufficient for public content. Private source requires verified repository-scoped read access before retrieval and before execution; re-check authorization at protected operations.

### Snapshot contract

Source retrieval resolves a repository identity and a full immutable commit SHA, then records a `RepositorySnapshot` with stable `snapshot_id`, repository identity, visibility, commit SHA, retrieval time, source provenance (provider/API or archive and authorization reference, never token), content manifest, per-file integrity metadata, and whole-snapshot integrity digest. Snapshot contents are immutable; refresh creates a new snapshot ID. A snapshot lifecycle is `resolving -> retrieving -> sealed -> expired/deleted` or a typed failure. Only `sealed` snapshots may be analyzed. Retention and deletion policy must account for private source and user/tenant ownership.

An execution pins one snapshot ID and commit SHA; all agents in that execution read from this snapshot through repository/intelligence interfaces. A workflow that intentionally refreshes source must create a new snapshot and a new execution or an explicitly versioned transition. No agent reads a moving branch implicitly. Verify manifest/digest integrity before use. Ingestion performs parsing and safe inventory only; it does not execute repository code, install dependencies, run hooks, or follow repository-supplied commands. Treat filenames, source, metadata, and model-directed instructions found in source as untrusted data.

## 4. GitHub authorization architecture

Keep read, write, and pull-request permissions distinct:

1. **Repository read:** contents and minimum metadata required to resolve identity, branch, and commit. Public reads require no account authorization.
2. **Repository write:** narrowly scoped permission to create a branch and write its contents. Used only by the publishing service after explicit approval.
3. **Pull request:** permission to create a PR (and minimum metadata to validate its target). Used only after approval. Do not assume a token or scope provides all three permissions.

Prefer a GitHub App with fine-grained repository permissions and repository selection. For CodeWise, use the App installation as the preferred durable repository-level grant where the user installs/selects the target repositories; use GitHub App user authorization when a user-bound identity/consent is needed to discover or validate access, not as a reason to give agents credentials. Public read can remain anonymous. The access adapter may use an installation token for repository-scoped reads where a private repository is selected. The publishing service obtains a short-lived installation token with write and pull-request permissions only for the approved target. GitHub App user authorization and installation authorization are separate grants; verify the effective permission set and repository scope for every operation. Avoid broad account-wide OAuth scopes. Exact GitHub App permission names and callback mechanics are implementation-time checks against current GitHub platform requirements.

Repository access service owns URL resolution, visibility and permission verification, source retrieval, and snapshot creation. Publishing service owns target verification, branch/commit operations, patch application, push, and PR creation. Credentials are held by a credential broker/secret store and are never passed to agents, prompts, frontend responses, or logs. Agents 1–8 receive snapshot and approved capability interfaces only. Agent execution and GitHub authorization/publishing are separate control planes. A connected account never implies consent to publish; every publish requires a separate explicit confirmation.

## 5. Patch publishing protocol

The patch artifact is immutable and identified by `patch_id`, bound to the originating execution, repository identity, snapshot ID/commit, patch digest, and review/test evidence. A publication approval binds an approval ID to the exact patch digest, target repository ID, base branch and resolved base commit SHA, creating user, timestamp, and an idempotency key. The UI/API must present these exact values before approval.

After approval, the publishing service revalidates authorization, repository identity, base branch, patch digest, and snapshot relationship. It creates a unique working branch (never the base branch), applies only the approved patch, verifies resulting tree/diff, pushes, and creates a PR with execution and evidence references. Any change to repository, base branch/commit, or patch digest invalidates approval and requires fresh approval. Never trust agent-provided target URLs as authorization.

Persist a publication state machine: `approval_pending -> approved -> branch_created -> patch_applied -> pushed -> pull_request_created`, with terminal `rejected`, `failed`, or `cancelled` outcomes. Record each transition and provider operation ID. Retries use an idempotency key scoped to user, patch, repository, and approved target. Before retrying an uncertain push/PR result, query for the deterministic branch/PR marker and reconcile rather than duplicating. Branch names include a stable publication identifier. On partial failure, retain the branch when safe, report its exact status, and allow an explicit retry or cleanup; never claim a PR exists until confirmed. Cleanup is limited to branches created by this publication and never force-pushes over user changes. Audit approval and every write operation.

## 6. Proposed versioned API contracts

These are transport-neutral proposed JSON shapes under `/api/v1/`; Django/DRF serializers and domain services remain implementation choices. IDs are opaque stable strings except SHA/digest fields. All authenticated endpoints enforce user/tenant ownership and object-level authorization. Responses contain no credential or token.

| Operation | Request | Response / principal validation |
|---|---|---|
| `POST /repositories/submissions` | `{ "url": string }` | `{ "submission_id": string, "repository": RepositoryIdentity, "visibility": enum, "access_status": enum, "snapshot": SnapshotRef|null }`; normalize GitHub URLs, reject unsupported hosts, resolve identity, do not leak private existence to unauthorized callers. |
| `POST /repositories/{repository_id}/access-verifications` | `{ "required_permissions": ["read"] }` | `{ "repository_id": string, "visibility": enum, "access_status": enum, "permissions": [enum], "verified_at": datetime }`; caller may verify only owned/authorized connection, otherwise return authorization-required without exposing private metadata. |
| `POST /executions` | `{ "submission_id": string, "snapshot_id": string, "agent_group_ids": [string], "idempotency_key": string }` | `{ "execution_id": string, "snapshot_id": string, "commit_sha": string, "agent_ids": [string], "status": enum }`; group IDs must be known and unique; mandatory agents are server-added; snapshot must be sealed and accessible. |
| `GET /executions/{execution_id}` | none | `{ "execution_id": string, "status": enum, "snapshot_id": string, "commit_sha": string, "agent_results": [{"agent_id":string,"status":enum,"artifact_refs":[string]}], "created_at": datetime, "updated_at": datetime }`; enforce ownership; redact source/secrets. |
| `GET /github/connection` | none | `{ "connected": boolean, "account": {"id":string,"login":string}|null, "installations": [{"installation_id":string,"repository_ids":[string],"permissions":[string]}] }`; expose grant metadata only. |
| `POST /github/authorizations` | `{ "purpose": "repository_read"|"publish", "repository_id": string|null, "return_path": string }` | `{ "authorization_id": string, "authorization_url": string, "state_expires_at": datetime }`; purpose/scope validated and state is single-use, expiring, session-bound. |
| `DELETE /github/connection` | none | `{ "disconnected": boolean }`; revoke/disconnect available grants where supported and invalidate local tokens; do not imply remote installation removal if not performed. |
| `POST /patch-publications/approvals` | `{ "patch_id": string, "repository_id": string, "base_branch": string, "base_commit_sha": string, "patch_digest": string, "idempotency_key": string, "confirm": true }` | `{ "publication_id": string, "status": "approved", "approved_target": {...} }`; require explicit confirmation, ownership, reviewed patch, and exact target binding. |
| `POST /patch-publications/{publication_id}/branches` | `{ "idempotency_key": string }` | `{ "publication_id": string, "branch": string, "head_commit_sha": string, "status": enum }`; internal/service-to-service operation, require approved immutable target and write permission. |
| `POST /patch-publications/{publication_id}/pull-requests` | `{ "idempotency_key": string, "title": string, "body": string }` | `{ "publication_id": string, "pull_request_url": string, "number": integer, "status": "pull_request_created" }`; require pushed branch, approved base, PR permission, idempotent reconciliation. |

`RepositoryIdentity`, `SnapshotRef`, and enum values are versioned shared contracts, not agent-private shapes. Common errors use `{ "error": { "code": string, "message": string, "retryable": boolean, "correlation_id": string } }`. Validation errors are `400`; unauthenticated `401`; insufficient permission `403` (or privacy-preserving `404` for private repository disclosure); conflict/stale approval or changed base `409`; rate limit `429` with retry guidance; upstream provider failure `502/503`; internal safe error `500`. Never include tokens, private source, or sensitive provider payloads in errors. Retry only when marked safe and with the same idempotency key.

## 7. Security, reliability, and dependency boundaries

- OAuth/App callback state is cryptographically unpredictable, single use, short lived, bound to initiating user/session and intended repository/purpose; validate exact redirect URI and reject replay. Apply CSRF protections to browser mutations and validate `Origin`/same-site session protections as appropriate.
- Encrypt tokens at rest with managed keys; use a secret manager/KMS-backed envelope strategy, short-lived least-privilege tokens, rotation, redaction, and revocation handling. Secrets never enter frontend responses, agent context, prompts, ordinary logs, traces, or artifacts.
- Enforce tenant/user ownership and repository ownership/installation scope on every read and write. Isolate private snapshots and derived indexes by authorization domain; deny access after revocation and expire/delete data under retention policy.
- Treat repository content and patch metadata as untrusted prompt-injection-capable input. Ingestion is non-executing. Test/build/command execution is separately authorized and occurs only in `sandbox/` with resource limits, restricted network, disposable isolation, and no ambient GitHub credentials. Agents request typed capabilities; they never receive raw credentials or unrestricted filesystem/network handles.
- Snapshot provenance and digest are checked before agent use and publishing. Audit authorization changes, access checks, snapshot creation/deletion, approvals, branch writes, PR creation, revocation, and failures with actor, repository ID, execution/publication IDs, outcome, and correlation ID; omit secrets and source bodies.
- Rate-limit URL resolution, authorization initiation, repository reads, executions, and publishing; handle provider quotas with bounded backoff and explicit retryable errors. Authorization revocation/expiry immediately blocks subsequent private reads and writes. Fail closed if authorization, integrity, or target validation cannot be established.
- API/frontend depend on application services; agents depend on Agent Foundation plus repository/intelligence and approved tool interfaces; repository access owns GitHub reads/snapshots; publishing owns GitHub writes; gateway owns model access; sandbox owns controlled execution; orchestration coordinates execution and approval. No agent imports GitHub SDK/credentials or calls repository provider APIs directly.

## 8. Decisions and open implementation details

Decisions: anonymous read for public analysis; repository-scoped read authorization for private source before snapshot/execution; GitHub App with fine-grained installation permissions as the preferred access/publishing architecture, with user authorization reserved for user-bound consent/identity needs; isolated read and publishing services; exact-commit immutable snapshots; mandatory Agents 1–2 and group-only optional selection; default-on optional groups; separate explicit approval and branch/PR publication; no direct base-branch push; no credential access by agents.

Alternatives rejected: requiring GitHub connection for all public analysis adds needless authorization and interrupts the primary flow; sending OAuth credentials to agents violates least privilege and couples reasoning to provider APIs; using a broad OAuth token for read and write conflates permissions; writing directly to the selected base branch removes review and recovery boundaries; mutable branch-only analysis makes results irreproducible. A GitHub App installation requires repository installation/selection and setup UX, but gives the clearest fine-grained repository scope and server-side automation model. Exact permission names, whether a given operation needs user authorization versus installation authorization, private-repository disclosure behavior, retention durations, branch naming, and webhook/revocation mechanisms must be verified and finalized during implementation against current GitHub capabilities and product policy.
