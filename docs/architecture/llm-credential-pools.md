# LLM Credential Pools

## Status

The gateway now supports three disabled-by-default credential pools. Pool routing is an extension inside `backend/llm_gateway/`; the existing single-route gateway and LangChain/LangGraph interfaces remain available. There are no configured providers, models, quota values, credentials, or production secret-manager adapters in this checkout. Pool settings must be supplied by the deployment operator before a pool can run.

## Stable agent assignment

The code owns this fixed mapping. It is not configurable from agent requests:

| Pool | Stable agent IDs |
| --- | --- |
| Pool A (`pool_a`) | `business_logic_analyst`, `code_quality_analyst` |
| Pool B (`pool_b`) | `security_analyst`, `migration_planner`, `patch_author` |
| Pool C (`pool_c`) | `test_engineer`, `review_agent` |

`structure_analyzer` is not in the mapping. Agent Foundation rejects both `requires_llm=True` and the `llm.invoke` permission for that ID, execution rejects an LLM-enabled context for it, and the gateway rejects its ID in both pooled and legacy route paths. Unknown IDs and requests naming another pool fail closed. Agents do not receive a pool credential or credential resolver in their context.

## Configuration and credentials

`PoolConfigurations` requires separate `PoolConfiguration` entries for all three pools. Each enabled pool must define ordered `PoolRoute` entries, local `PoolLimits`, shared `QuotaLimits`, and the supported retry policy. A route has a provider/model route and an ordered credential tuple: entry zero is primary, later entries are fallback credentials. Route task and capability declarations are validated before any credential is resolved.

`LLMGateway.from_environment` reads secret-free JSON pool configuration from `CODEWISE_LLM_POOL_A_CONFIG`, `CODEWISE_LLM_POOL_B_CONFIG`, and `CODEWISE_LLM_POOL_C_CONFIG`. Credential entries contain references such as `CODEWISE_LLM_CREDENTIAL_<OPERATOR_NAME>`; the corresponding value is read from the backend process environment by `EnvironmentCredentialResolver`. The pool config stores references, not values. Missing or malformed environment credentials fail gateway construction for an enabled pool. The root `.gitignore` excludes `.env` files. A local launcher may load an ignored environment file into the backend process; this code does not parse `.env` files itself.

`SecretManagerResolver` is a production integration protocol only. No cloud secret manager, key vault, production resolver, provider adapter, or secret rotation service is configured here. The gateway resolves a credential for an invocation and passes it to the provider adapter as `SecretStr` within that call. Credential values are not part of requests, responses, model metadata, execution context, or workflow state. The active secret is redacted from provider response content and metadata before the response enters the agent path. Provider exceptions and configuration errors are replaced with safe gateway errors.

## Quota identity and accounting

Credential identity (`credential_id` and `secret_ref`) is separate from `QuotaIdentity` (`provider_id`, account, organization, project, and operator-assigned `quota_bucket_id`). Credential IDs/references cannot be shared between pools. Credentials declared for the same provider/account/organization/project must use the same quota-bucket ID. Accounting keys use provider plus quota-bucket ID, so an operator can assign one shared bucket across multiple credentials, projects, or pools when those credentials share real capacity. The gateway never infers capacity from key count or a ChatGPT, Gemini, or other subscription.

An operator must set local pool and quota-accounting limits. These are admission limits, not representations of a provider's published quota. Pool request/token/concurrency limits are separate for A, B, and C. Quota-bucket counters are shared across pools when the same bucket identity is configured, preventing two pool configurations from treating shared capacity as independent. If the same bucket is configured with differing limits, the ledger uses the stricter limit for each field. All counters and locks are process-local; multi-worker deployments need a separately approved shared ledger before claiming global enforcement.

Pool A, B, and C never borrow a credential or pool budget from one another. A request's stable agent ID selects exactly one pool; an optional requested pool ID must match it. Fallback candidates are built only from that pool's ordered routes and credentials.

## Fallback and error policy

Provider adapters normalize upstream failures to `ProviderCallError` categories. Eligible categories are rate limit, quota exhausted, temporary outage, retryable network error, and transient server error. Authentication, invalid request, unsupported model/capability, invalid structured output, safety refusal, and permanent errors stop without fallback. Unknown adapter exceptions are treated as permanent and their text is never returned.

- Rate-limit/quota errors do not retry the exhausted credential. The current quota identity is skipped for the remainder of that invocation; fallback proceeds only to a different declared quota identity.
- Temporary provider outages and transient server errors trip the per-pool/provider circuit and skip that provider for the rest of that invocation.
- Retryable network errors may retry the same credential within the pool's configured bound.
- Any retry or fallback requires `GatewayRequest.idempotent=True`; it is false by default. LangChain model factories must set it only for an operation they have explicitly deemed safe to repeat.
- Adapters convert a valid upstream `Retry-After` value to `retry_after_seconds`. The gateway respects it as a minimum wait, combines it with bounded exponential backoff and jitter, and refuses to retry when the delay exceeds the configured safe wait or request deadline.
- Same-account/project keys are not retried as separate capacity after that quota identity is exhausted. Multiple distinct account/project identities can be used only when explicitly configured in the same pool.

`PoolExhausted`, `PoolBudgetExceeded`, and `ProviderRejected` expose stable safe codes and pool/failure-kind metadata, never a secret or raw provider message. Circuit state is per pool/provider; quota accounting is shared by quota identity.

## Paid usage and current limits

Paid routes require all three gates: gateway policy enabled, `GatewayPolicy.allow_paid=True`, and the pool's `paid_authorized=True`; the paid tier must also be enabled. All gates default closed. No paid API use is enabled by this implementation, and no subscription is interpreted as provider API quota.

The environment loader and pool contracts are implemented, but no real provider adapters, route values, account/project identities, budgets, credentials, retry mappings, production secret manager, or distributed quota backend are supplied. Operators must configure those values without copying keys into code, documentation, prompts, agent contexts, results, logs, or checkpoints. Tests use synthetic identities and fake providers only.
