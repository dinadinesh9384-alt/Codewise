# CodeWise V2 production readiness and deployment boundary

**Status: not production ready.** This guide records the implemented controls and the infrastructure and reliability work still required. Configuration and documentation do not mean that production services have been provisioned or verified.

## Readiness summary

| Priority | Finding | State |
| --- | --- | --- |
| Production blocker | No production `CODEWISE_API_WORKFLOW_FACTORY` implementation is configured or supplied. The readiness endpoint returns unavailable without a usable runtime. | Runtime contract and env setting prepared; deployment implementation absent. |
| Production blocker | API executions run synchronously inside the HTTP request. A process loss can leave an execution in `running`; there is no worker, recovery coordinator, or durable execution queue. | Not implemented. |
| Production blocker | No persistent LangGraph checkpointer is configured by this repository. Approval requires a runtime and graph with durable checkpoint support. | The API fails closed; production pause/resume is not verified. |
| Production blocker | PostgreSQL, a TLS-terminating reverse proxy, a production web server/container, and a private persistent storage volume are not available in this workspace. | Settings and a sample environment are prepared; no deployment has occurred. |
| Production blocker | Login has no application rate limiter or account recovery, and there is no workspace creation UI. | A guarded one-time workspace provisioning command exists; upstream rate limiting and account recovery remain operational requirements. |
| Production blocker | Snapshot expiry and abandoned staging cleanup have service functions but no scheduled production job. | Cleanup operations exist; scheduler/monitoring absent. |
| High | Backup/restore, multi-process execution, database locking, and concurrent approval have not been exercised against PostgreSQL. | Runbooks below; infrastructure validation pending. |
| High | No request or provider metrics, alert rules, distributed tracing, or operational dashboard is configured. | Execution IDs and safe structured Django logs exist; metrics/alerts are absent. |
| High | No browser automation runner or browser binary is installed. | Browser strategy documented; browser E2E not run. |
| Medium | The API has no CORS configuration. | Same-origin deployment is the supported configuration; cross-origin browser deployment is unsupported. |
| Medium | The Vite production build is generated, but no production static/API hosting configuration is included. | Build verified; hosting is an operator task. |
| Optional | Add reviewed Content Security Policy, central log retention, and provider-specific operational dashboards. | Not configured. |

## Implemented and tested

- Django settings default to `DEBUG=False` and fail closed in production without a sufficiently long, varied `DJANGO_SECRET_KEY`, explicit non-wildcard `DJANGO_ALLOWED_HOSTS`, PostgreSQL, TLS database settings, positive HSTS, HTTPS redirects, and an explicit absolute private snapshot volume.
- Session and CSRF cookies are HttpOnly, Secure outside development, and SameSite Lax. Proxy protocol trust is opt-in and documented as safe only behind a trusted proxy that overwrites the header and blocks direct application access.
- Django session login/logout and authenticated workspace discovery use the existing `User` and `WorkspaceMembership` models. Login is CSRF-protected; failed credentials return one generic response. There is no account creation endpoint.
- The `provision_initial_workspace` management command provisions exactly one marked initial workspace for an explicit active superuser, assigning owner membership through `create_workspace`. It is idempotent for the same user/name/description and rejects ambiguous existing data. See [backend setup](../backend/README.md) for the exact Windows CMD invocation.
- `/api/v1/ready/` checks database connectivity and instantiates the configured workflow runtime. It returns only safe status codes and component names. It separately reports whether the runtime advertises durable approval checkpoints. `/api/v1/health/` remains a process liveness response.
- Django request/security log messages use JSON output and redact common credential-shaped fields. This is a defense in depth formatter, not a guarantee that arbitrary message text is safe.
- The API remains workspace-scoped; approval requires owner/admin authorization and revalidates snapshot, Agent 8 provenance, proposal identity, and runtime checkpoint support.
- Snapshot storage uses opaque IDs, rejects links and unsafe paths, writes private files, verifies manifests, and uses staged publication. Repository code is analyzed as data and is not executed.
- LLM pool configuration is disabled by default, paid routes require explicit gateway and pool authorization, credentials are resolved server-side, and cross-pool fallback is rejected.
- SQLite development behavior is unchanged. No existing database was replaced or removed.

The current validation run uses Django's test database and fake/offline providers. It does not prove behavior under real infrastructure.

## Implemented but not tested against real infrastructure

- PostgreSQL settings use `DJANGO_DB_*`, persistent connections, health checks, and TLS verification. `psycopg` is declared in `backend/requirements.txt`; the installed development environment has not been switched to PostgreSQL.
- Production static serving and same-origin reverse proxying are described but no Nginx, load balancer, container, or web-server configuration is included.
- The private storage root can be mounted as a persistent volume and uses restrictive POSIX permissions. Volume ownership, filesystem durability, capacity alarms, and restore behavior need deployment verification.
- A runtime can be selected by dotted import path through `CODEWISE_API_WORKFLOW_FACTORY`. No production runtime factory is supplied. Readiness must remain unavailable until this factory creates an object with a usable `workflow_for` method.

## Documented, not implemented

- Durable worker queue, durable LangGraph checkpointer configuration, interrupted-run reconciliation, safe retry/resume policy, worker shutdown/drain behavior, and execution leases.
- Edge rate limiting for `/api/v1/session/login/`, account recovery, and administrative user/workspace provisioning.
- Scheduled snapshot expiry/deletion and abandoned staging cleanup. Existing retention code conservatively retains snapshots with execution bindings; cleanup must account for this policy.
- PostgreSQL backup scheduling, point-in-time recovery, restore drills, database migration rollout coordination, and monitoring of snapshot-storage consistency.
- Metrics/alerts for execution age and failures, agent lifecycle, provider status, pool budgets, storage capacity, and backup freshness.
- Actual browser E2E coverage, session renewal/expiry behavior in a browser, and reverse-proxy cookie/TLS validation.

## Environment configuration

Start from [`backend/.env.example`](../backend/.env.example) and inject values through the deployment secret manager. Do not commit populated environment files. Generate a new Django secret using a cryptographically secure random generator, for example `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Never reuse a development key.

Required production configuration includes:

- `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and HTTPS `DJANGO_CSRF_TRUSTED_ORIGINS`.
- `DJANGO_SECURE_SSL_REDIRECT=true`, positive `DJANGO_SECURE_HSTS_SECONDS`, and secure cookies. Enable HSTS include-subdomains/preload only after all covered hostnames are permanently HTTPS-capable.
- `DJANGO_TRUST_X_FORWARDED_PROTO=true` only if the trusted ingress strips caller-provided forwarding headers, writes its own, and is the only network path to Django. Otherwise leave it false and terminate TLS directly at Django's trusted server.
- `DJANGO_DB_ENGINE=django.db.backends.postgresql`, database name/user/password/host/port, and `DJANGO_DB_SSLMODE=verify-full` (or an explicitly reviewed `verify-ca`/`require`). Supply `DJANGO_DB_SSLROOTCERT` if the managed database needs a private CA.
- An absolute, private, persistent `CODEWISE_PRIVATE_REPOSITORY_STORAGE` path outside static/public roots, owned by the Django service account and backed up with snapshot metadata.
- A valid `CODEWISE_API_WORKFLOW_FACTORY` import path. An empty or broken factory leaves readiness unavailable. Approval also requires a durable checkpoint implementation.
- A same-origin frontend/API host. Leave `VITE_API_BASE_URL` unset. No CORS middleware or cross-site cookie policy is configured.

The login route must be rate-limited at a trusted ingress before exposing it publicly. This repository does not include a distributed authentication throttle.

## LLM provider configuration

Keep all provider credentials in the backend secret manager/environment. Never place provider keys in `VITE_*` variables or browser bundles.

The gateway accepts JSON pool definitions in `CODEWISE_LLM_POOL_A_CONFIG`, `CODEWISE_LLM_POOL_B_CONFIG`, and `CODEWISE_LLM_POOL_C_CONFIG`; credential values are separate variables referenced by `secret_ref` (for example, `CODEWISE_LLM_CREDENTIAL_<SLOT>`). Pool configuration must satisfy the strict contracts in `backend/llm_gateway/pool_contracts.py`.

- Pool A: Agents 2 and 3.
- Pool B: Agents 4, 5, and 6.
- Pool C: Agents 7 and 8.
- Agent 1 remains LLM-free.

All pools and paid access remain disabled unless the application runtime explicitly enables a policy and the pool config authorizes the route. Test fallback, quotas, retries, and concurrency with synthetic providers before enabling any real provider. This task made no live or paid LLM calls.

## Deployment sequence (operator checklist)

1. Provision PostgreSQL with TLS, automated backups, private network access, a least-privilege application role, and reviewed connection limits.
2. Provision the Django secret, database credentials, runtime factory config, LLM pool secret references, and a private persistent snapshot volume. Do not put secrets into frontend build variables.
3. Configure an HTTPS ingress that serves the React build and proxies `/api/` to Django on the same origin. Set restrictive request size/time limits; allow enough time for current synchronous execution only if accepting its worker exhaustion risk. Apply login rate limiting.
4. Provision a supported Django WSGI/ASGI server and pin its dependency/configuration. No production server package or container image is included here.
5. In a release environment with production settings, run `python manage.py check --deploy`, `python manage.py migrate`, and `python manage.py collectstatic --noinput`. Review warnings and the exact migration plan before applying it.
6. Start the API and static host, verify `/api/v1/health/` and `/api/v1/ready/`, inspect cookie flags through HTTPS, and run authenticated smoke checks for login, workspace listing, repository submission, and permission denial.
7. Before accepting real analysis traffic, validate the production runtime, provider limits, synchronous request timeouts, durable approval behavior, storage retention schedule, and alerting. Readiness is not evidence that a workflow has been run.

Do not expose Django's development server or Vite's development/preview server as production services.

## Database migration, backup, and restore

Apply migrations as a release step with a reviewed database backup. Keep SQLite as the local development/test database; never point a production process at `backend/db.sqlite3`.

Example PostgreSQL backup from an authorized operations host (credentials supplied via a protected `PGPASSFILE`/secret manager, not command-line arguments):

```sh
pg_dump --format=custom --file="codewise-$(date -u +%Y%m%dT%H%M%SZ).dump" "$PGDATABASE"
```

Restore a backup into a new, isolated database and verify migrations, workspace membership, execution/snapshot bindings, audit records, and manifest digest checks before any cutover:

```sh
createdb "$RESTORE_DATABASE"
pg_restore --dbname="$RESTORE_DATABASE" --no-owner "$BACKUP_FILE"
```

Snapshot files and the PostgreSQL database must be backed up as a coordinated set. Preserve file ownership and private permissions. The database records snapshot references and hashes; restoring only one side can make results unavailable or integrity checks fail. No backup job or restore drill is implemented or verified here.

## Snapshot retention and recovery

Private/internal snapshots receive an expiry time and are denied for new reads after expiry. `expire_due_snapshots`, `delete_expired_snapshot`, and staging cleanup helpers exist, but no scheduler invokes them. Production must schedule and monitor expiry/deletion and staging cleanup, retry rows left in `deleting`, and alert on storage failures. Existing code conservatively avoids deleting a snapshot with execution bindings because binding completion state is insufficient to prove safe removal.

## Workflow execution and approval

The current API executes LangGraph synchronously in the request process and persists agent progress/final results in PostgreSQL/SQLite. This is not a worker queue. A hard process termination between state writes can leave an execution running without a recovery path. Do not automatically rerun it: providers may have performed non-idempotent work and approval state may be ambiguous.

The configured runtime must supply persistent, deployment-supported LangGraph checkpointing for approval pause/resume. The test-only in-memory saver is not production persistence. The application deliberately rejects requested approval when the durable checkpoint capability is absent. A production queue, durable resume worker, execution lease/reconciliation, and concurrency tests remain blockers; implement them as a reviewed design before production analysis traffic.

Human approval records an authenticated owner/admin decision and resumes the workflow only through the backend. It never applies or publishes a patch. AI review readiness is not human approval.

## Monitoring and incident handling

- `/api/v1/health/` is a liveness response only.
- `/api/v1/ready/` checks database connectivity and runtime factory instantiation; it reports checkpoint availability separately and does not expose exception messages. It is not a provider quota, snapshot-storage, or end-to-end workflow check.
- Django request/security and `codewise` logger messages are JSON. The formatter redacts common credential patterns only; code must not log passwords, request bodies, source code, repository contents, tokens, prompt content, or raw provider responses.
- Execution IDs, selected agents, lifecycle statuses, workflow error codes, and audit events are persisted. No metrics exporter, dashboard, or alert policy is supplied.
- On provider incidents, disable the affected pool/policy in the runtime and preserve execution/audit identity. Never move credentials or quota capacity across pools as an ad hoc fallback.

## Rollback

1. Stop new submissions at ingress and drain/stop web and worker processes (a worker implementation is not yet provided).
2. Deploy the previous application version only if it is compatible with the applied database schema. Prefer forward migrations for additive changes.
3. If data restoration is required, restore PostgreSQL and the matching snapshot volume into isolated targets, verify integrity, and then cut over. Do not restore a database without its snapshot data or vice versa.
4. Recheck `/health/`, `/ready/`, session/CSRF, permissions, and a synthetic-provider workflow before reopening traffic.

No rollback against production data or deployed infrastructure has been run.

## Browser E2E strategy

Playwright, Puppeteer, Selenium, browser executables, and a browser test runner are not present in the current environment. The existing frontend suite is Node-based and mocks API responses; it is not browser E2E.

Before launch, provision an isolated PostgreSQL test service and a browser runner, run Django with a fake repository provider and fake LLM gateway, seed test users/workspaces, and automate: login/CSRF token rotation, workspace selection, repository submission, synchronous execution/status rendering, partial/unavailable agent results, approval and rejection confirmation, unauthorized workspace access, logout, and expired sessions. Capture browser console and network failures. Never point the suite at production data or a paid provider.
