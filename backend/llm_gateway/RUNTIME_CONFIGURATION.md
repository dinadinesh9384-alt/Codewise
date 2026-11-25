# Workflow runtime configuration

The default API workflow factory is `config.workflow_runtime.build_api_workflow_runtime`. The factory builds the existing Django snapshot reader, explicit eight-agent registry, LangGraph workflow, gateway-backed LangChain models, and provider adapters. It does not read model credentials into a configuration model. Credential references are resolved by the gateway only at call time.

## Required environment settings

For a deny-all runtime with all pools disabled, these are valid placeholder-only settings:

```dotenv
CODEWISE_API_WORKFLOW_FACTORY=config.workflow_runtime.build_api_workflow_runtime
CODEWISE_LLM_POLICY_CONFIG={"enabled":false,"allowed_agents":[],"allowed_models":[],"enabled_tiers":[],"allow_paid":false}
CODEWISE_LLM_MODEL_CAPABILITIES={"groq":{},"gemini":{}}
CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=false
# Optional: v1, v1beta, or v1alpha. The pinned Google SDK 3.x defaults to v1.
# CODEWISE_GEMINI_API_VERSION=v1beta
```

If a pool environment value is absent or blank, that pool stays disabled. The strict pool JSON shape is defined by `PoolConfiguration`, `PoolRoute`, `CredentialReference`, `QuotaIdentity`, `PoolLimits`, and `QuotaLimits` in `pool_contracts.py`. A route uses a verified model ID and a backend secret reference; it never contains the credential value. Example fragments below are templates and must not be used to claim that a model supports a feature or to activate a real provider:

```dotenv
# capability manifest: provide only entries verified for exact provider model IDs
CODEWISE_LLM_MODEL_CAPABILITIES={"groq":{"<verified-groq-model-id>":["max_output_tokens","structured_output","system_messages"]},"gemini":{"<verified-gemini-model-id>":["max_output_tokens","structured_output","system_messages"]}}

# each pool has its own independent JSON document; keep enabled=false until reviewed
CODEWISE_LLM_POOL_A_CONFIG={"enabled":false,"routes":[],"limits":null,"quota_limits":null,"paid_authorized":false}
CODEWISE_LLM_POOL_B_CONFIG={"enabled":false,"routes":[],"limits":null,"quota_limits":null,"paid_authorized":false}
CODEWISE_LLM_POOL_C_CONFIG={"enabled":false,"routes":[],"limits":null,"quota_limits":null,"paid_authorized":false}

# only set a secret value in the backend secret store/process environment
CODEWISE_LLM_CREDENTIAL_<NAME>=<secret-managed-outside-source-control>
```

For an enabled pool, policy must explicitly allow the pool's assigned agent IDs, the exact `provider_id`/`model_id` route pair, and its non-paid tier. `allow_paid=true`, any enabled `paid` tier, `paid_authorized=true`, or any configured paid route is rejected. All routes (including staged disabled routes) are checked against the capability manifest. Credentials, credential references, and quota identities cannot be shared across pools under the existing pool model.

The pinned Google Gen AI SDK 3.x uses the Gemini Developer API `v1` endpoint by default. Set optional `CODEWISE_GEMINI_API_VERSION` to `v1`, `v1beta`, or `v1alpha` when a reviewed Gemini model/API combination requires an explicit version. The setting is passed as `HttpOptions.api_version`; no model alias or API version is selected automatically by the application. Verify the exact model against the selected API version before enabling its route.

## PostgreSQL checkpoint setup

Durable approval resume is opt-in and supported only on the configured PostgreSQL database:

```dotenv
CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=true
```

After installing the pinned backend requirements and applying Django migrations, initialize LangGraph's own checkpoint tables with:

```bat
python manage.py setup_langgraph_checkpoints
```

The command only runs when checkpointing is explicitly enabled. It does not modify Django models or migrations. Runtime startup verifies the checkpoint schema but never creates it implicitly. If the pool, PostgreSQL connection, checkpointer dependency, or schema is missing, runtime configuration fails safely. Approval execution remains unavailable until all requirements are met.

The saver is held in a bounded process-local connection pool. LangGraph thread IDs are the database-backed workflow execution UUIDs, reused for resume. The serializer only permits JSON-safe primitive graph values; tokens and provider credentials are never put into workflow state. Analysis results and patch/review data do persist in checkpoint storage, so database security, retention, backups, and transport protection are deployment responsibilities.

### Approval outbox and recovery

An authenticated owner/admin request is fully validated against the stored execution, immutable snapshot, Agent 6 proposals, Agent 8 readiness/provenance, and proposal identity before an immutable `ApprovalOutboxEvent` is created. The event, stable operation UUID, actor, decision, snapshot UUID, proposal IDs, request fingerprint, and queued audit event commit in one Django transaction. One event is allowed per execution. Identical retries resolve to that event; a different decision or proposal set conflicts.

Outbox processing leases one event at a time. The worker verifies the event and current execution/snapshot identity and revalidates Agent 6/8 data before touching LangGraph. It uses the execution UUID as thread ID. If the expected approval interrupt remains, it resumes that interrupt; if the checkpoint already records the same decision, it continues the same graph from that checkpoint. Any foreign execution, snapshot, interrupt, or decision state fails closed. Retryable failures retain the actor-attributed command; expired leases can be reclaimed. After 12 unsuccessful attempts the event becomes failed and requires operator investigation.

Approval decisions, execution result/status, final actor-attributed audit event, and outbox completion are committed together in a Django transaction. The checkpointer uses a separate Psycopg connection pool and transaction; there is no atomic commit spanning checkpoint and Django writes. If checkpoint advancement succeeds and Django finalization rolls back, the event is retryable and the next worker reconciles from the advanced checkpoint. If the process stops before checkpoint advancement, the lease expires and retry resumes the pending interrupt. This provides durable retry/reconciliation, not cross-database atomicity or an exactly-once guarantee for arbitrary external side effects.

Run `python manage.py process_approval_outbox --limit 25` from a supervised scheduled worker to recover pending, retryable, or expired processing events. Processing is also attempted inline after the durable enqueue. The worker only records human decisions; the workflow has no permission to apply patches or publish repositories.

## Compatibility and validation limits

`langgraph-checkpoint-postgres==3.1.3` requires `langgraph-checkpoint>=4.3.0,<5`; the pinned `langgraph==1.2.14` permits that range. The PostgreSQL checkpointer is not installed in the shared virtual environment at present, and no PostgreSQL service is available for integration validation here. Offline tests exercise runtime composition, pool binding, and fail-closed behavior; they do not verify a live Postgres saver connection or resume. Groq and Google Gen AI SDK pins also need exact-version testing in a disposable/safe environment before deployment.
