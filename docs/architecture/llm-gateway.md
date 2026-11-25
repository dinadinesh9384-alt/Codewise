# LLM Gateway

## Scope and boundary

`backend/llm_gateway/` is the only model transport boundary. It owns typed request and response contracts, route selection, policy checks, request limits, structured response validation, retries, circuit breaking, and safe usage events. Agents must receive a `GatewayChatModel` from the trusted `ExecutionLifecycle` model factory. Provider adapters implement `ProviderAdapter` and are called only by `LLMGateway`.

There is no provider adapter or model route enabled by default. The default policy denies invocation. The gateway retains its single-route compatibility path and adds an optional three-pool path; pooled mode maps each official LLM agent to exactly one pool and resolves referenced credentials only inside the gateway call. See [LLM Credential Pools](llm-credential-pools.md) for the mapping, config, quota and fallback behavior. A deployment must explicitly supply providers, routes, pool policy and credentials. `paid` routes additionally require the gateway and pool authorization flags; both default false. No actual provider configuration is included.

`structure_analyzer` remains deterministic and cannot be declared as an LLM agent. The seven official reasoning agent IDs are the only IDs accepted by the gateway. `llm.invoke` is checked by the model boundary and again by the gateway.

## Contracts and enforcement

Requests carry messages, optional JSON Schema, task/capability requirements, route information, trace identifiers, deadline, output-token cap, and optional cost ceiling. In pooled mode, the stable agent ID determines the pool; a conflicting requested pool is denied. Pydantic v2 rejects unknown fields. Structured responses are validated with JSON Schema before returning to an agent. Provider metadata is bounded and credential-like key names are rejected. Active credential values are redacted from pooled provider output before agent delivery. Prompts, source content, API keys, and provider exception text are not sent to telemetry.

The gateway enforces input byte limits, output-token limits, per-minute rate limits, per-execution and per-agent request ceilings in a rolling hour, a process-local concurrency ceiling, optional per-request cost ceilings, and a provider circuit breaker. Routes declare supported tasks and structured-output capability. Cost is calculated only when route prices and actual token counts are available. When a caller sets a cost ceiling, the gateway checks a conservative pre-call bound from input bytes and maximum output tokens; if route pricing is unknown, it denies the request. Unknown token usage and cost remain explicitly unavailable. The byte-based bound is conservative admission accounting, not a provider tokenizer.

Legacy single-route retries default to zero. In pooled mode, fallback is ordered by configured routes and credentials, with typed failure eligibility, shared quota identity checks, bounded retry-after/backoff and per-pool circuit state. Repeated attempts require an idempotent request. A provider adapter must normalize upstream failures and honor the supplied timeout; the gateway cannot forcibly terminate an uncooperative provider thread.

Counters, rate limits, concurrency limits, and circuit state are in memory and process-local, including shared quota-bucket accounting across the three pools within one process. They are not a distributed quota or durable ledger. The package adds no database, Redis dependency, billing estimate, production credentials store, or secret-manager implementation. Deployments requiring cross-worker budgets must supply an approved shared enforcement service before enabling model traffic.

## LangChain integration

`agent_foundation.adapters.langchain_adapter.GatewayChatModel` subclasses LangChain Core `BaseChatModel`. It converts LangChain messages into gateway contracts and delegates every request to the gateway. `with_structured_output` is a Runnable that requests schema validation from the gateway and returns either a dictionary or a validated Pydantic object. `to_langchain_tool` adapts an existing typed Agent Foundation tool and still invokes it through the foundation permission and output checks. There are no provider-specific LangChain packages in the requirements.

LangChain Core, LangGraph, and JSON Schema versions are pinned in `backend/requirements.txt` for Python 3.11. These dependencies provide framework integration only; they do not configure a provider, route, credential, or paid model.

## Configuration and tests

Construct the gateway in trusted server composition code. `LLMGateway.from_environment` loads the three secret-free pool JSON settings and environment credential references; it does not enable the gateway or paid usage by itself. Production secret loading must inject a separately reviewed implementation of the `SecretManagerResolver` protocol. Do not build provider clients from agent input or repository content. Never put provider credentials in `ExecutionContext`, graph state, request contracts, or logs.

Gateway and LangChain tests use controlled in-process fake adapters. They verify deny-by-default behavior, permission and tier checks, structured schema validation, usage-unavailable handling, cost and input limits, gateway-only model invocation, and the absence of provider SDK imports in other backend packages. The tests make no external model calls.
