# CodeWise V2 — Proposed Directory Architecture

## 1. Purpose and status

This file, `docs/architecture.md`, is the single canonical architecture document for CodeWise V2. It describes the proposed V2 architecture and records implemented boundaries where noted; it is not a claim that every proposed subsystem exists.

CodeWise V2 is independent of V1. V1 may be consulted as reference material, but V1 files and behavior must not be modified as part of V2 architecture work.

## 2. Canonical specialist agents

Exactly eight specialist agents live under `backend/agents/`:

| Number | Directory | Stable internal ID | LLM requirement |
|---|---|---|---|
| 1 | `01_structure_analyzer` | `structure_analyzer` | Deterministic; no LLM required |
| 2 | `02_business_logic_analyst` | `business_logic_analyst` | Meaningful LLM reasoning |
| 3 | `03_code_quality_analyst` | `code_quality_analyst` | Meaningful LLM reasoning |
| 4 | `04_security_analyst` | `security_analyst` | Meaningful LLM reasoning |
| 5 | `05_migration_planner` | `migration_planner` | Meaningful LLM reasoning |
| 6 | `06_patch_author` | `patch_author` | Meaningful LLM reasoning |
| 7 | `07_test_engineer` | `test_engineer` | Meaningful LLM reasoning |
| 8 | `08_review_agent` | `review_agent` | Meaningful LLM reasoning |

The numeric directory prefix is an organizational ordering convention, not the agent's programmatic identity. Internal IDs, API values, graph node IDs and persisted records must not depend on parsing the directory name.

Agent 1 performs deterministic repository inventory and structural analysis. It must not require an LLM to perform its core responsibilities. Agents 2–8 must perform substantive reasoning through the LLM gateway; merely importing or initializing an LLM client does not satisfy this requirement. Deterministic tools and validators should support those agents.

Each agent owns its private prompts, internal schemas, tools, tests and implementation details. Cross-agent data exchange must use versioned shared contracts rather than importing another agent's private modules.

## 3. Shared Agent Foundation

### Purpose and status

The Shared Agent Foundation is a logical, reusable platform capability for the eight specialist agents. It standardizes their execution boundary and cross-cutting operational behavior while leaving specialist intelligence and internal design with each owning agent. It is not a ninth agent and does not own a domain-specific analysis function. This specification defines the intended architecture; it does not claim that the foundation or its interfaces are implemented.

The physical package location is implemented as `backend/agent_foundation/`, consistent with the backend execution boundary. The package provides narrow protocols and composable services rather than a monolithic base-agent class. Agents may adopt only the capabilities their responsibilities require. A deterministic agent does not acquire an LLM stage simply to conform to a common lifecycle. The package must not introduce circular dependencies or make `shared/` a home for executable agent internals. See [Shared Agent Foundation](architecture/agent-foundation.md) for implementation status and boundaries.

### Responsibilities and interfaces

The foundation standardizes:

- A stable agent registration and invocation interface, lifecycle hooks, configuration validation, and execution outcomes. Stable agent IDs come from the canonical registry and never from parsing directory names.
- Versioned input and output contracts. Pydantic-based models validate agent-private runtime payloads at agent boundaries; only deliberately designed cross-subsystem contracts belong in `shared/contracts/`. API serializers and workflow state remain separately owned as defined elsewhere in this document.
- An execution context carrying the stable agent ID, unique execution ID, correlation/trace context, deadline or timeout budget, cancellation signal, policy-bound capabilities, and references to repository snapshot/version. Context contains references and scoped metadata, not an unrestricted service locator or a second canonical data store.
- Structured evidence and artifact references with provenance, repository/version identity, source location where available, and integrity metadata. Large artifacts are referenced through approved storage interfaces rather than copied into logs or unbounded agent results.
- Tool registration, strict input/output schemas, invocation mediation, permission checks, policy enforcement, and uniform timeout, error, and audit handling.
- Structured logging, tracing, metrics, and execution metadata, with redaction and data-handling controls applied before telemetry leaves the relevant trust boundary.
- Consistent error categories and execution outcomes for success, validation failure, policy denial, tool/model failure, timeout, cancellation, and retryable interruption. Retry decisions are bounded, policy-controlled, and must not repeat unsafe side effects without idempotency or explicit authorization.
- Shared testing conventions and optional LLM integration adapters that route exclusively through `llm_gateway`.

The foundation owns execution plumbing and cross-cutting controls. Each agent owns its specialist reasoning, domain-specific components and tools, interpretation of its output, and domain-specific validation. Foundation validation checks contract shape and general policy; it does not claim that an answer is substantively correct.

### Registration and configuration

Each agent is registered under its stable internal ID from the canonical agent roster. Directory names are organizational and must never be parsed to derive identity. A registration describes the agent, its implementation/API version, supported input and output contract versions, declared capabilities (for example, deterministic analysis, model reasoning, or named tool classes), and required platform interfaces. Metadata is descriptive and supports compatibility checks; it does not grant permissions.

Registration and configuration are trusted control-plane inputs. Agents should be discovered through an explicit, trusted registration mechanism; scanning arbitrary repository content or importing untrusted plugins is outside the foundation boundary. Whether registration is static, settings-driven, or supplied by a future registry service remains unresolved. Common configuration is validated before enablement, while agent-specific configuration is validated against a schema owned by that agent. Secrets are supplied as protected references through approved configuration services, not embedded in manifests, prompts, or logs.

An agent is eligible to execute only when it is registered and enabled, its declared contract versions are compatible with the invocation, required platform capabilities are available, and current authorization and policy checks succeed. Eligibility is checked at execution time where permissions or policy can change. Registration alone never authorizes data access, tool invocation, model use, or execution.

### Execution context

The context is a per-invocation, scoped envelope rather than a service locator or repository-data container. It carries the stable agent ID and execution ID; correlation and trace identifiers; the requested operation and validated execution configuration; repository snapshot/version references when relevant; scoped permissions/capabilities; references to approved shared artifacts; a deadline/resource budget; and a cancellation signal. User identity or tenant references are included only when required and authorized.

Pass immutable identifiers and evidence/artifact references where possible, then let agents retrieve bounded content through authorized platform interfaces. Do not place an entire repository, credentials, unrestricted filesystem/network handles, global mutable state, or arbitrary service clients in the context. Context serialization for checkpoints must be explicit, bounded, versioned, and governed by privacy and retention policy; live secrets and process-local handles are not checkpoint state.

### Contract and validation architecture

Agent input and output contracts are owned by the agent; tool contracts are owned by the tool provider. Pydantic-based validation is the common runtime validation mechanism at invocation boundaries where Python payloads are used, but the foundation accepts caller-supplied model/schema definitions rather than defining every agent's domain schema. The foundation validates contract shape and declared version; each agent remains responsible for semantic and domain validation. Cross-subsystem payloads use versioned definitions in `shared/contracts/` where appropriate. Reuse those definitions instead of copying them into foundation or agent packages. API transport serializers and LangGraph state are adapters/state, not replacements for canonical contracts.

Every exchanged contract has an explicit schema/version identity. Compatibility is determined from declared supported versions and an explicit adapter or migration policy; silently coercing unknown versions is prohibited. Validation failures use a normalized error category with contract identifier/version and safe field-level diagnostics. They must not include secrets or dump untrusted payloads into logs. Breaking changes require a new contract version and coordinated compatibility handling; detailed versioning and deprecation policy remains an implementation decision.

### Agent lifecycle

An invocation follows these applicable stages, with optional stages omitted when the agent does not need them:

1. **Register and configure:** resolve the canonical agent ID and validate agent configuration, enabled capabilities, and applicable policy version.
2. **Validate input:** parse the versioned input contract and reject malformed or unsupported requests before side effects.
3. **Create execution context:** assign an execution ID, bind repository snapshot and correlation metadata, establish a deadline and cancellation signal, and expose only scoped capabilities.
4. **Check permissions and policy:** authorize requested tools, data access, model use, and controlled execution before each relevant action. A registration is not itself authorization.
5. **Perform specialist work:** invoke deterministic components, permitted tools, and, when required by that agent, meaningful LLM reasoning. Long-running or stateful work may delegate transitions to an agent-local graph as justified below.
6. **Validate output:** validate the agent's output contract and apply agent-owned semantic checks. Invalid output becomes a typed failure or a bounded repair/retry path only when policy permits.
7. **Collect evidence and metadata:** associate evidence/artifact references, tool outcomes, validation outcomes, and applicable model metadata with the execution.
8. **Produce the result:** return a versioned structured result and execution status through the agent interface. Orchestration decides subsequent workflow actions.
9. **Handle error or cancellation:** honor cancellation and deadlines at safe boundaries, stop further work, preserve a truthful partial/failure status and permitted evidence, and release scoped resources. Retries are not implicit and must follow the retry and side-effect policy.

The ordering is a common contract, not a requirement that every invocation perform every stage. For example, Agent 1 can validate input, inventory deterministically, collect evidence, validate its result, and return without model setup or invocation.
Lifecycle transitions produce a structured execution outcome and lifecycle events. A stage may be skipped only when it is not applicable and the agent's declared capability/policy permits that omission. Input validation and applicable authorization precede side effects. Output validation and evidence association precede a successful final result. A partial result must be explicitly marked partial and identify completed/failed stages; it must never be represented as a complete success.

### LangChain and LangGraph responsibilities

**LangChain** supplies composable model, prompt, structured-output, and standardized model/tool interfaces where useful inside an agent. Any model integration used by LangChain must be an adapter to `llm_gateway`; agents must not configure provider credentials, instantiate unmanaged provider clients, or bypass gateway policy, quotas, privacy controls, routing, caching, and observability. Prompt templates and versions remain owned by the agent when specialist-specific, or by `templates/` when genuinely reusable. Structured model output is untrusted until validated against the relevant schema and domain checks. LangChain is an integration toolkit, not the agent lifecycle manager or platform workflow orchestrator.

**LangGraph** owns stateful graph execution where a workflow benefits from explicit nodes and transitions. Platform-level graphs in `orchestration/` compose agents and platform capabilities, route between them, manage workflow state, checkpoint/recover according to policy, and coordinate human approval. An agent may use a small local graph for its own multi-step specialist process when this materially improves state handling, conditional execution, or recovery. Such a graph remains inside the agent boundary, does not call another agent directly, and does not replace platform orchestration. A simple linear agent should use direct components rather than acquire a graph abstraction without need. Checkpoint contents and persistence remain subject to workflow-state ownership and security policies.

Agents integrate these capabilities through narrow interfaces and adapters. They need not all use LangChain or LangGraph internally. Shared execution contracts and policy enforcement remain consistent even when an agent uses deterministic Python components or another permitted internal composition.

### Deterministic and LLM-reasoning agents

`01_structure_analyzer` uses the foundation for registration, input/output validation, scoped repository context, tools, evidence, observability, and error handling, but its core repository inventory and structural analysis are deterministic and must not require an LLM invocation. It must remain usable when no model provider is configured or available.

Agents `02_business_logic_analyst` through `08_review_agent` must perform substantive, task-relevant reasoning through `llm_gateway`. Merely importing a model service, initializing a client, adding a nominal LLM step, or echoing deterministic findings does not meet this requirement. The model must contribute meaningful analysis to the agent's specialist result. Deterministic Python components remain essential across all agents for scanning, parsing, evidence collection, policy checks, schema validation, calculations, and verification; model reasoning does not replace them. Model unavailability must be surfaced according to policy rather than silently represented as completed LLM analysis.

LangChain is an optional agent-facing integration layer for suitable model, prompt, structured-output, and tool abstractions. Its model adapter must call `llm_gateway/`; provider credentials, provider selection, quota handling, policy, and fallback remain gateway responsibilities. Validate structured responses against the requested schema and the agent’s semantic rules before accepting them. Gateway-controlled transport retries and fallback are permitted only when gateway policy marks the operation eligible and budgets allow it. Agent-level retries must not blindly repeat model/tool side effects or disguise an unavailable model as completed reasoning. The foundation normalizes invocation outcomes; it does not implement provider routing or an independent fallback system.

All paid API calls remain disabled absent explicit user authorization. No provider free tier, quota, or consumer subscription is assumed. Provider selection, model selection, and local-model requirements remain governed by the deferred decisions and `llm_gateway` policies.

### Tool architecture and controls

Tools are explicit capabilities invoked through a common typed boundary. Each tool declares a stable identifier, purpose, strict input/output schema, required permissions, data sensitivity, side-effect class, timeout, and error mapping. Validate inputs before invocation and outputs before returning them to an agent. Enforce least privilege and policy at invocation time, record an auditable outcome, and propagate cancellation and time limits. Tool errors must be typed and observable without leaking secrets or untrusted content into logs. Tools require independent unit and contract tests, plus integration and failure/timeout coverage appropriate to their effects.

Tool definitions are registered from trusted agent or platform owners and are discoverable only within the invoking execution’s authorized capability set. Registration records stable tool identity/version, owner, strict input/output contract, required permission, data sensitivity, side-effect classification, timeout/resource limit, and error mapping. Discovery is not authorization: enforce permissions immediately before every invocation and validate both request and response. Apply execution-specific deadlines and cancellation; resource-intensive or repository-controlled work must use its owning boundary. Normalize tool failures and retain invocation metadata without logging sensitive inputs or outputs. Never turn the tool dispatcher into an unrestricted command runner.

Three categories are recognized:

1. **Agent-private tools:** owned and tested by one agent for specialist tasks; private schemas and implementation remain within that agent.
2. **Shared platform tools:** reusable capabilities exposed through explicit platform interfaces, such as repository retrieval, code graph queries, and evidence lookup. Their ownership remains with the corresponding platform subsystem (`intelligence/`, `repository_engine/`, or another authorized owner), rather than being copied into every agent.
3. **Controlled execution tools:** tests, builds, commands, and patch validation that may execute repository-controlled content or cause side effects. These must pass through authorization and the secure `sandbox/` boundary and relevant `verification/` capabilities. No agent or tool may silently execute repository content directly.

MCP may be adopted for suitable external integrations when its protocol and operational trade-offs are justified. Internal Python tools do not all need to use MCP; they may use direct typed interfaces behind the same permission, validation, observability, and execution controls.

### Recommended agent-local structure

The following is a recommended adaptable organization, not a requirement to create every directory or file now:

```text
<agent>/
├── components/   # specialist analysis and domain logic
├── tools/        # agent-private tool adapters and schemas
├── schemas/      # private input, output, and intermediate models
├── prompts/      # versioned specialist prompts, when LLM use applies
├── services/     # agent-level composition and platform adapters
├── tests/        # unit, contract, tool, and integration tests
└── README.md
```

An agent may omit empty or irrelevant areas and add narrowly justified internal modules. It may add `workflows/` when it owns a genuinely multi-step specialist process with meaningful conditional transitions, resumability, or local state. Agent-local workflows remain private to that agent and are invoked through the agent interface. Cross-agent routing, end-to-end workflow state, human approval, and platform checkpoint/recovery belong to `orchestration/`, not an agent's `workflows/` directory. Do not duplicate orchestration graphs in agents or create agent-to-agent imports.

### Proposed internal package structure

This is the conceptual boundary map for `backend/agent_foundation/`, not a commitment to create every module as a separate Python package. Keep the implementation cohesive; combine small responsibilities when that reduces indirection. Implemented package modules and remaining integration work are documented in [Shared Agent Foundation](architecture/agent-foundation.md). Agent-specific components, tools, prompts, and schemas remain in each agent. Shared cross-subsystem definitions remain in `shared/`.

| Proposed module | Responsibility and owns | Must not own | Dependencies and agent use |
|---|---|---|---|
| `registry.py` | Registration records, stable-ID lookup, metadata/config validation, enablement and compatibility checks. | Agent implementations, authorization decisions, or dynamic imports from untrusted sources. | Depends on shared identity/contracts only where already defined and injected trusted configuration. Agents are resolved through their stable IDs. |
| `execution.py` | Invocation boundary, applicable lifecycle sequencing, execution outcome/result envelope, and coordination of common hooks. | Specialist analysis, domain workflows, platform graph definitions, or a mandatory base class. | Depends on narrow context, validation, tool, error, and observability interfaces. Agents expose/consume the invocation contract through composition. |
| `context.py` | Scoped execution-context types, deadline/resource budget and cancellation propagation, correlation metadata, and safe artifact references. | Repository content stores, unrestricted service locators, secrets, or global mutable state. | Uses shared types/contracts where canonical definitions already exist. Agents receive only the context for their execution. |
| `validation.py` | Generic Pydantic/schema validation adapters, contract version checks, and normalized validation diagnostics. | Duplicated shared schemas or agent-specific semantic correctness rules. | Accepts agent/tool-owned schemas and shared contract definitions by reference. Agents call it at input/output boundaries and still perform domain validation. |
| `tools.py` | Trusted tool registration metadata, scoped discovery, pre-invocation permission checks, schema validation, timeout/cancellation mediation, and normalized invocation records. | Specialist tool implementations, platform capability internals, or direct unrestricted command execution. | Depends on injected authorization and tool-provider interfaces; controlled execution delegates to `sandbox/` and applicable `verification/` services. Agents receive only declared tool handles. |
| `errors.py` | Stable foundation-level error categories, safe diagnostics, retryability hints, and mapping from adapter failures. | Deciding every retry, swallowing failures, or asserting that a side effect is safe to repeat. | Depends on shared error taxonomy if one is defined there; otherwise exposes a narrow adapter boundary for later alignment. Agents return typed outcomes for orchestration/policy handling. |
| `observability.py` | Lifecycle event and trace/metric emission interfaces, privacy-aware metadata shaping, and correlation propagation. | Owning a telemetry vendor/backend, evaluation scoring, or retaining raw repository/prompt content. | Emits through injected observability interfaces; `evaluation/` may consume permitted telemetry but production foundation code must not depend on evaluation internals. Agents emit execution and evidence references through this boundary. |
| `integrations/langchain.py` (optional) | Adapter from approved LangChain model/prompt/structured-output interfaces to `llm_gateway/`. | Provider clients, credentials, independent routing/quotas, or an LLM dependency for all agents. | Optional integration depends on LangChain and the gateway interface. Only model-using agents adopt it; Agent 1 need not import or initialize it. |
| `integrations/langgraph.py` (optional) | Thin adapter for invoking an agent execution capability from a LangGraph node and mapping typed outcomes/cancellation. | Platform graph definitions, cross-agent routing, canonical workflow state, checkpoint storage, or approval policy. | Optional integration depends on stable execution interfaces and LangGraph. `orchestration/` owns platform graphs; agents use a local graph only when justified. |
| `tests/` | Independent tests of foundation interfaces, policies, adapters, lifecycle, and compatibility behavior. | Tests that require specialist intelligence to be implemented before foundation contracts can be verified. | Uses controlled fakes/fixtures for platform ports; agent compatibility suites can plug in each agent’s declared contracts. |

The module list is a starting boundary map, not a required one-file-per-capability layout. In particular, no foundation-owned `schemas/` package should duplicate agent schemas or `shared/contracts/`, and no `workflows/` package should duplicate `orchestration/` graphs.

### Testing and execution observability

Every agent and shared foundation interface should be independently testable. Expected coverage includes:

- Unit tests for deterministic components, schema/domain validation, lifecycle logic, and error mapping.
- Contract tests for registration, invocation, versioned inputs/outputs, and shared tool interfaces.
- Tool tests for permission checks, schema rejection, side effects, redaction, and expected outcomes.
- Integration tests for permitted interactions with `llm_gateway`, repository/intelligence services, sandbox, and orchestration as applicable.
- Failure, timeout, cancellation, retry-boundary, and unavailable-service tests.
- Deterministic behavior tests for deterministic components and Agent 1, using controlled inputs and stable evidence expectations.
- LLM-dependent tests that distinguish model-assisted behavior from deterministic checks and can use controlled fixtures or explicitly configured test doubles without claiming a paid provider is available.
- Lifecycle tests for applicable stage ordering, skipped optional stages, permissions-before-side-effects, context scoping, result status, and cleanup.
- Compatibility tests proving registered agents satisfy declared invocation and contract versions without importing one another; test Agent 1 with no LLM adapter configured.
- Adapter tests for gateway, LangChain, LangGraph, observability, and sandbox boundaries using controlled fakes; separately gated integration tests may exercise configured services.


### Reliability and error handling

Normalize failures into categories such as invalid input/output, incompatible contract, policy denial, unavailable capability, tool failure, gateway/model failure, timeout, cancellation, resource-limit exhaustion, and internal foundation failure. Preserve safe causal metadata and distinguish retryable transport interruption from non-retryable validation, authorization, or side-effect failure.

Retries are bounded and occur only when the operation and failure are explicitly eligible under policy, the execution deadline/budget permits them, and any side effect is idempotent or protected by an explicit deduplication/authorization mechanism. `llm_gateway/` owns provider retry and fallback behavior; the foundation does not retry around it without a defined policy. Do not repeat controlled execution merely because a result is missing or ambiguous. Fallback is eligible only through gateway policy and must preserve the declared task and validation requirements.

Timeouts and cancellation propagate through lifecycle stages and tool calls. Stop starting work after cancellation/deadline, request cooperative cancellation from active adapters, and classify work that cannot be interrupted until it returns. Security-sensitive permission, policy, and sandbox checks fail closed when their decision service is unavailable. Preserve truthful partial outcomes and completed evidence, but do not claim an unverified result as success. Recovery/checkpoint policy belongs to `orchestration/`; the foundation exposes serializable execution status and safe boundaries but does not persist or resume platform workflows itself. An agent failure is isolated to its invocation outcome; orchestration applies workflow policy to decide whether other work continues, retries, or aborts.

Each execution should emit or make queryable, subject to privacy and retention policy: stable agent ID, execution ID, correlation/trace ID, input/output contract versions, repository snapshot/version, start time and latency, execution status, policy and validation outcomes, tool identifiers and outcomes, evidence/artifact references, and typed errors. For model-backed calls, record gateway-provided provider/model metadata, request outcome, latency, token usage, and cost information when available; record resource usage when the runtime exposes it. Never log credentials, unrestricted source payloads, or sensitive prompt content by default. Observability must preserve the distinction between a proposed result, a successful tool invocation, and independently verified evidence.

## 4. Complete proposed directory tree

```text
CodeWiseV2/
├── backend/
│   ├── agents/
│   │   ├── 01_structure_analyzer/  # deterministic Agent 1 implemented
│   │   └── registry.py             # explicit stable-ID registration
│   ├── agent_foundation/      # shared agent execution foundation
│   ├── config/
│   ├── apps/
│   ├── api/
│   ├── authentication/
│   ├── domain_services/
│   ├── consumers/
│   ├── jobs/
│   ├── manage.py                 # Django management entry point
│   └── README.md
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── app/
│   │   ├── pages/
│   │   ├── layouts/
│   │   ├── components/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── state/
│   │   ├── services/
│   │   ├── charts/
│   │   ├── animations/
│   │   ├── styles/
│   │   └── tests/
│   └── README.md
├── orchestration/
│   ├── graphs/
│   ├── nodes/
│   ├── routing/
│   ├── state/
│   ├── checkpoints/
│   └── policies/
├── intelligence/
│   ├── code_graph/
│   ├── retrieval/
│   ├── repository_twin/
│   ├── context/
│   └── reasoning/
├── llm_gateway/
│   ├── providers/
│   ├── routing/
│   ├── quotas/
│   ├── caching/
│   ├── policies/
│   └── observability/
├── repository_engine/
├── sandbox/
├── verification/
│   ├── static_analysis/
│   ├── patch_validation/
│   ├── test_verification/
│   └── regression_analysis/
├── evaluation/
│   ├── datasets/
│   ├── benchmarks/
│   ├── metrics/
│   ├── experiments/
│   └── reports/
├── security/
│   ├── policies/
│   ├── redaction/
│   └── threat_model/
├── shared/
│   ├── contracts/
│   ├── events/
│   ├── exceptions/
│   ├── types/
│   └── protocols/
├── templates/
│   ├── agent_prompts/
│   ├── reports/
│   ├── notifications/
│   └── exports/
├── infrastructure/
│   ├── development/
│   ├── containers/
│   └── observability/
├── docs/
│   ├── architecture/
│   ├── security/
│   └── development/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── end_to_end/
├── scripts/
└── README.md
```

## 5. Major directory responsibilities

### `agents/`
`backend/agents/` contains exactly eight specialist agents. Agent 1 is implemented at `backend/agents/01_structure_analyzer/`; its scope and limits are documented in [Structure Analyzer](architecture/agents/01-structure-analyzer.md). Agents 2–8 remain planned. Each agent is an independently testable capability with its own implementation, prompts, private schemas, tools and tests. Agents return structured outputs and evidence references through shared contracts.

### `backend/agent_foundation/`
Shared execution infrastructure for all eight specialist agents: common invocation and lifecycle interfaces, context and metadata propagation, generic contract validation hooks, evidence/artifact references, tool mediation and policy integration, error handling, testing conventions, and observability integration. The foundation owns cross-cutting execution plumbing only; specialist intelligence, agent-specific components, tools, prompts, and semantic output interpretation remain in their respective agent packages. See [Shared Agent Foundation](architecture/agent-foundation.md) for current implementation status.

### `orchestration/`
Owns LangGraph workflow composition and execution control:
- `graphs/`: top-level and subgraph definitions.
- `nodes/`: orchestration nodes that call agents or platform capabilities.
- `routing/`: conditional routing and deterministic dispatch.
- `state/`: typed workflow state and transition definitions.
- `checkpoints/`: persistence adapters and resume/recovery policy interfaces.
- `policies/`: retry, timeout, approval, cancellation and escalation rules.

Orchestration state is transient or checkpointed workflow state. It is not the canonical application database model and must not become a dumping ground for domain data.

### `intelligence/`
Provides reusable repository intelligence:
- `code_graph/`: symbols and relationships across files and repository layers.
- `retrieval/`: hybrid retrieval combining semantic, keyword/BM25, symbol/AST-aware retrieval, dependency traversal and reranking.
- `repository_twin/`: versioned, incrementally refreshed repository representation.
- `context/`: evidence selection, provenance and bounded context assembly.
- `reasoning/`: reusable evidence synthesis utilities not owned by a single agent.

### `llm_gateway/`
Provides provider-independent model access:
- `providers/`: provider adapters.
- `routing/`: task-based model selection and eligible fallback.
- `quotas/`: quotas, rate limits and budget controls.
- `caching/`: validated caching and deduplication.
- `policies/`: privacy, data handling and model-use restrictions.
- `observability/`: token, latency, error, provider and cost telemetry.

Paid API calls must remain disabled unless the user explicitly authorizes them. A subscription to a consumer chat product must not be assumed to include API quota.

### `repository_engine/`
Owns repository acquisition, safe file inventory, immutable snapshot creation, parsing coordination, file identity and repository-version tracking. Repository content is untrusted input and must never be executed merely to analyze it.

### `sandbox/`
Owns isolated and explicitly authorized execution environments. It must enforce resource limits, network restrictions, disposable workspaces and execution policy. Analysis and test execution must not silently execute arbitrary repository content.

### `verification/`
Contains independently testable verification capabilities:
- `static_analysis/`: deterministic static checks and adapters.
- `patch_validation/`: patch scope, integrity, formatting and policy checks.
- `test_verification/`: test discovery, execution-result validation and evidence classification.
- `regression_analysis/`: regression and compatibility analysis.

A proposed patch, a successful tool invocation and a verified test result are different evidence states and must be represented separately.

### `evaluation/`
Measures system quality independently of product execution:
- `datasets/`: versioned evaluation data.
- `benchmarks/`: repeatable benchmark definitions.
- `metrics/`: quality, reliability, latency, cost and outcome metrics.
- `experiments/`: controlled model, prompt, retrieval and workflow experiments.
- `reports/`: evaluation summaries and regression reports.

### `security/`
Owns platform-wide security policies, redaction rules and threat-model documentation. Agent 4 performs specialist analysis; this directory owns reusable platform security controls rather than duplicating that agent.

### `shared/`
Contains only stable cross-subsystem contracts and small shared primitives:
- `contracts/`: versioned public data-transfer schemas.
- `events/`: event names and envelopes.
- `exceptions/`: shared error taxonomy.
- `types/`: genuinely shared primitive types.
- `protocols/`: subsystem interfaces.

It must not become a shared dumping ground for agent-private schemas or Django ORM models.

### `backend/`
Django + Django REST Framework application boundary:
- `config/`: Django settings, root URL composition and ASGI/WSGI configuration.
- `apps/`: business-capability Django apps and their application database models.
- `api/`: versioned REST endpoints, serializers, viewsets, routers and API permissions.
- `authentication/`: identity, authentication and authorization integration.
- `domain_services/`: application use cases and coordination between persistence and platform capabilities.
- `consumers/`: authenticated WebSocket consumers for live progress and notifications.
- `jobs/`: background task definitions and dispatch integration.

Django ORM models belong to backend apps, not `shared/contracts`, agent schemas or orchestration state. API serializers are transport adapters, not replacements for canonical shared contracts.

### `frontend/`
React + JavaScript presentation layer:
- `app/`: app composition, route registration and providers.
- `pages/`: route-level screens.
- `layouts/`: shared page shells.
- `components/`: reusable UI primitives.
- `features/`: feature-oriented modules.
- `hooks/`: reusable React hooks.
- `state/`: client state management.
- `services/`: REST and WebSocket clients.
- `charts/`: reusable data visualizations.
- `animations/`: reusable Motion animation definitions.
- `styles/`: Tailwind entry points, global styles and design tokens.
- `tests/`: frontend tests.

The frontend must not access the database or call LLM providers directly. It communicates through backend APIs and authenticated WebSocket channels.

### `templates/`
Preserves reusable prompt, report, notification and export templates. Agent-specific prompt logic can remain in the owning agent package; this directory is for genuinely reusable cross-agent or platform templates. Template files must be versioned and tested.

### `infrastructure/`
Holds development, container and observability configuration. Infrastructure definitions must not silently enable paid services or unrestricted execution.

### `docs/`, `tests/`, `scripts/`
`docs/architecture.md` is the single canonical architecture document. `docs/architecture/` is reserved for ADRs, diagrams and supporting architecture documents; `docs/security/` and `docs/development/` organize those subjects. Tests are divided by unit, integration, contract and end-to-end scope. Scripts are explicit developer/maintenance utilities and must not be implicit production execution paths.

## 6. Data and schema ownership boundaries

| Data category | Owner | Rule |
|---|---|---|
| Cross-subsystem contracts | `shared/contracts/` | Versioned, stable exchange formats |
| Agent-private schemas | Individual `agents/<agent>/` | Internal to the owning agent |
| Workflow state | `orchestration/state/` | Execution state, transitions and checkpoint shape |
| Application persistence models | `backend/apps/` | Django ORM models and database migrations |
| API transport schemas | `backend/api/` | HTTP request/response adaptation |
| Frontend view state | `frontend/src/state/` | UI-only state; never canonical backend data |

Do not create circular ownership. If a schema must cross subsystem boundaries, promote a deliberately designed versioned contract into `shared/contracts/` rather than importing private implementation details.

## 7. Dependency direction and allowed communication

The intended dependency direction is:

1. `frontend` communicates with `backend` only through documented REST/WebSocket interfaces.
2. `backend` coordinates use cases and persistence, and invokes orchestration/platform interfaces.
3. `orchestration` invokes agent interfaces and platform capabilities; it owns workflow control, not agent internals.
4. `agents` use their own private components and may consume `shared` contracts plus approved platform interfaces.
5. `intelligence`, `repository_engine`, `verification`, `sandbox`, `llm_gateway` and `security` expose explicit interfaces to authorized callers.
6. `shared` is a leaf-level contract layer and must not import backend, frontend, agent implementations or orchestration implementations.
7. `evaluation` observes and measures components through explicit instrumentation and test interfaces; production components must not depend on evaluation internals.
8. `templates` are loaded through explicit template interfaces; they must not import executable application code.

The dependency boundaries for `agent_foundation/` are:

- Agent implementations depend on the foundation’s execution interfaces while retaining ownership of specialist logic and private implementation.
- The foundation may use stable contracts, types, and protocols from `shared/`; it must not duplicate shared data definitions or import private agent modules.
- `orchestration/` owns platform-level graph composition, cross-agent routing, workflow state, checkpointing, recovery, and approval. It invokes agents through their agreed interfaces; the foundation does not own those workflows.
- `llm_gateway/` owns centralized model access, provider adapters, routing, quotas, and model-use policy. The foundation may provide common integration points, but all model calls by agents pass through the gateway.
- `sandbox/` and `verification/` retain ownership of controlled execution and verification capabilities; the foundation mediates access without executing repository content itself.

Agent 1 uses the foundation’s validation, context, evidence, tool, and observability capabilities without requiring LLM setup or invocation. Agents 2–8 must perform meaningful, task-relevant LLM reasoning through `llm_gateway/`; deterministic Python components remain essential, and merely importing or initializing a model client does not satisfy this requirement.

Avoid circular imports and direct cross-agent imports. Agent-to-agent collaboration must be mediated by orchestration using shared contracts. LLM access must go through `llm_gateway`; no agent should create unmanaged provider clients. Repository execution must go through the authorization and sandbox boundary.

## 8. Platform Dependency Architecture and Dependency Rules

### Purpose and decision

This section refines the dependency direction for the subsystems in this architecture and is consistent with the accepted placement in [ADR-001](architecture/adr-001-agent-foundation-placement.md). It distinguishes **code dependency** (imports or compile-time references) from **runtime capability interaction** (a call through a public interface). A runtime interaction does not grant permission to import another subsystem’s private implementation.

The recommended design is a directed, acyclic application dependency graph with stable contracts at subsystem boundaries and explicit capability injection at execution time. The platform composition layer wires implementations to interfaces; it does not make the foundation, agents, or shared contract layer import each other’s internals. This keeps components independently testable and lets platform implementations change without changing agent reasoning.

### Ownership and permitted dependency matrix

In this matrix, “required” means the dependency is part of the subsystem’s architectural role, not that a particular Python import or implementation already exists. “Optional/injected” means a subsystem may interact through an explicit public interface when a use case requires it. Runtime calls must still pass authorization and policy checks at the relevant boundary.

| Subsystem | Owns | Required dependency edges | Optional or injected edges | Prohibited edges |
|---|---|---|---|---|
| `shared/` | Stable versioned contracts, shared types, protocols, events, and error definitions. | None; it is a leaf layer. | None required. | Imports from every application subsystem, especially agent implementations, foundation, orchestration, backend, or frontend. No executable subsystem behavior or agent-private schemas. |
| `agent_foundation/` | Common agent registration/invocation interfaces, lifecycle plumbing, context, generic validation hooks, tool mediation, normalized outcomes, and observability ports. | None to a concrete platform implementation. | `shared/` definitions; optional adapters to `llm_gateway/`, security, repository/intelligence, sandbox, verification, and observability interfaces. | Any individual agent implementation; platform graph definitions/state stores; provider SDKs or unmanaged clients; direct repository-content execution; evaluation internals. |
| `agents/` | Specialist reasoning, private components/schemas/prompts/tools, semantic validation, and interpretation of specialist results. Exactly the eight registered agents. | `agent_foundation/` execution interfaces; `shared/` contracts when data crosses subsystem boundaries. | Authorized repository/intelligence, LLM, security, verification, or tool capabilities supplied through scoped interfaces. | Imports between agent implementations; provider SDKs/direct provider configuration; direct command execution; orchestration graph internals; persistence or telemetry implementation internals. |
| `orchestration/` | Platform LangGraph definitions, transitions, routing, workflow state, checkpoints/recovery policy, cancellation, and human approval. | Public invocation interfaces from `agent_foundation/`; `shared/` contracts for cross-subsystem state and messages. | Registered agent interfaces and authorized platform capabilities including `llm_gateway/`, repository/intelligence, `sandbox/`, `verification/`, and security policy interfaces. | Foundation or agent private internals; specialist logic; provider clients; direct execution outside `sandbox/`. |
| `llm_gateway/` | Central model access, provider adapters, model routing, quotas/budgets, model-use policy, caching, and model-call telemetry. | Its own provider adapter boundary and applicable shared contracts/types. | Security policy and observability interfaces; provider-specific SDKs confined to gateway adapters. | Agent implementations, provider configuration in agents, or an alternate model path that bypasses gateway policy. The gateway must not depend on agent or orchestration implementations. |
| `repository_engine/` | Repository acquisition, safe inventory, immutable snapshots, parsing coordination, identity, and repository-version tracking. | Its own storage/acquisition adapters and applicable shared contracts. | Security/redaction policy, infrastructure storage adapters, and interfaces consumed by intelligence or authorized callers. | Agent implementations, orchestration state ownership, untrusted repository execution, or dependency on `intelligence/` for its core acquisition boundary. |
| `intelligence/` | Repository code graph, retrieval, repository twin, bounded context and reusable evidence synthesis. | Repository snapshot/evidence interfaces from `repository_engine/`; shared contracts where information crosses subsystem boundaries. | Security filtering, approved indexes/stores through explicit adapters, and authorized consumers such as agents or orchestration. | Agent-private reasoning, model-provider calls outside `llm_gateway/`, orchestration workflow state, or execution of repository content. |
| `sandbox/` | Isolated, authorized execution environments and enforcement of execution/resource/network limits. | Security policy/authorization decisions and its execution-runtime boundary. | Infrastructure runtime adapters; requests from authorized tools, orchestration, or verification through a declared execution interface. | Agents or verification running commands directly; unrestricted host execution; repository-triggered self-authorization; dependence on agent internals. |
| `verification/` | Deterministic static analysis adapters, patch checks, test-result validation, and regression/compatibility evidence. | Its own verification contracts and shared evidence/result definitions where applicable. | `sandbox/` for any verification that executes code or commands; repository/intelligence evidence interfaces; security policy. | Direct execution of repository code, provider access outside `llm_gateway/`, agent-private modules, or treating a proposed patch/tool success as verified evidence. |
| `security/` | Platform-wide security policy, redaction, and threat-model controls. | Stable policy definitions and enforcement interfaces. | Consuming authorized configuration/secret-management interfaces; supplying decisions to foundation, gateway, repository, sandbox, verification, and orchestration boundaries. | Specialist Agent 4 implementation, application workflow ownership, or relying on prompts as enforcement. Security policy must not depend on a caller being well behaved. |
| `evaluation/` | Datasets, benchmarks, metrics, experiments, and reports measuring CodeWise quality. | Evaluation harness and stable instrumentation/test interfaces. | Read-only observation of permitted production telemetry and explicit public test interfaces. | Imports or runtime dependencies from production components into evaluation internals; access to secrets or unrestricted repository/prompt payloads. |
| `infrastructure/` | Development, container, and observability deployment/configuration. | Deployment/runtime configuration for selected services and external infrastructure. | Operationally packages or configures subsystems; provides adapters where separately owned interfaces require them. | Runtime application imports from infrastructure configuration or infrastructure becoming the owner of domain logic, contracts, agent behavior, or security policy. |

The matrix is a dependency policy, not a claim that all optional integrations are implemented. In particular, optional integrations must not become mandatory imports at module load time for agents that do not use them.

### Capability delivery and execution boundaries

Agents receive shared capabilities as scoped, typed interfaces bound to a single execution context. The foundation validates the registered capability declaration, exposes only authorized handles, and mediates lifecycle, schema validation, timeout/cancellation, and invocation metadata. Platform subsystems retain ownership of capability implementations: repository access remains in `repository_engine/` or `intelligence/`, model access remains in `llm_gateway/`, and controlled execution remains in `sandbox/` with verification owned by `verification/`. Agents do not receive a global service locator, unrestricted credentials, raw provider clients, or an ambient command runner.

Permissions are enforced at the operation boundary, not only when an agent is registered or a workflow begins. Check authorization for repository/artifact access, each tool invocation, each model request, and each sandbox execution using the applicable security policy. Recheck where permissions or policy can change. If a security-sensitive decision cannot be obtained, fail closed. The agent’s reasoning or a prompt cannot grant its own permission.

### Orchestration invokes agents

`orchestration/` invokes an agent through the stable registered-agent execution interface and stable agent ID, with versioned input contracts and a scoped execution context. It owns graph nodes, transitions, conditional routing, workflow state, checkpointing/recovery, cancellation propagation, and human approval. It may call platform capability interfaces as the workflow requires, but it must not import an agent’s private components or absorb its specialist reasoning.

The foundation owns agent-level invocation/lifecycle support, not the platform graph. Agent-local workflows remain inside the owning agent and do not route to other agents. Agent results return as structured outcomes and evidence references; orchestration chooses the next transition and preserves the distinction between agent output, tool execution, and independently verified evidence.

### Model request path

For model-using agents, the logical path is:

`agent specialist component -> approved LangChain/gateway adapter -> llm_gateway/ -> authorized provider adapter -> model provider`

The adapter may be supplied through the foundation’s optional integration, but `llm_gateway/` remains the sole owner of provider configuration, credentials, routing, quotas, fallback eligibility, privacy policy, and provider-facing calls. Structured responses return through the gateway/adapter boundary, are validated against the declared schema, and then receive agent-owned semantic validation. Retry and fallback are bounded by gateway policy and execution budgets; a gateway failure cannot be relabeled as successful reasoning. Agent 1 does not require model configuration and must remain executable when gateway configuration is absent. Paid API use is not presumed or enabled by this dependency design.

### Repository data, evidence, sandbox, and verification flow

`repository_engine/` produces identified, immutable snapshot/version references and safe inventory/evidence inputs. `intelligence/` may build indexes, graph relationships, retrieval results, and bounded evidence from those snapshots. Authorized agents receive only the evidence or content needed for the task through scoped interfaces; their outputs cite evidence/artifact references rather than copying an unbounded repository into shared context. `shared/contracts/` carries stable cross-subsystem evidence/result shapes where needed, while source data and indexes remain owned by their subsystem.

Read-only analysis does not execute repository content. If a workflow requests a test, build, command, or other controlled execution, the request passes through permission checks and `sandbox/` isolation with resource and network limits. `verification/` may request execution through that boundary and then classifies the resulting evidence; it does not launch commands itself. The workflow returns execution status and evidence references through contracts to the agent/orchestrator. A successful sandbox invocation alone is not proof that a patch or behavior is correct.

### Shared contracts and cycle prevention

`shared/` is a leaf contract layer. A definition belongs there only when it is a deliberate, stable exchange format or primitive used across subsystem boundaries. Agent-private schemas stay with their agent; workflow state stays with `orchestration/`; application persistence and API transport models stay in `backend/`; foundation runtime behavior stays in `agent_foundation/`. Do not solve a dependency cycle by moving arbitrary implementation code into `shared/`.

When two subsystems need to communicate, define or reuse a narrow protocol/contract at the correct boundary and inject the implementation. The implementation depends on the protocol; the protocol does not import the implementation. Cross-agent data flows through orchestration and versioned contracts, never direct agent-to-agent imports. This dependency inversion supports testing with fakes without creating duplicate schemas or a generic framework for every interaction.

### Automated dependency enforcement

Before implementation, define executable architecture rules and run them in continuous integration. A static import-graph check should enforce at least these invariants:

- `shared/` imports no application subsystem.
- `agent_foundation/` imports no individual agent, `orchestration/`, `evaluation/`, provider SDK, or sandbox execution implementation; optional adapters have explicit allow-listed edges.
- No agent imports another agent or provider SDK, and Agent 1 can import/run without model integrations configured.
- `orchestration/` imports public interfaces only, not private agent implementations; `llm_gateway/` is the only provider-facing package.
- `repository_engine/` does not depend on `intelligence/`; `verification/` and agents cannot execute repository code outside `sandbox/`.
- Production packages do not import `evaluation/`; infrastructure configuration is not imported by runtime domain packages.

Static import rules should be complemented by contract/integration tests that exercise capability injection, permission checks, gateway-only model calls, cancellation, and sandbox-only controlled execution. An allow-listed exception must name its owner and rationale and receive architecture review; broad package-wide exclusions defeat the rule. The specific static-analysis tool and CI integration remain implementation choices.

### Design alternatives and recommendation

Two common alternatives were considered. First, letting `agent_foundation/` import concrete gateway, repository, sandbox, telemetry, and agent implementations can make early wiring appear simple, but couples the lifecycle core to every platform package, invites circular imports, and makes deterministic Agent 1 load optional model dependencies. Second, forcing every capability through `orchestration/` centralizes calls but makes agent execution depend on platform workflow code and turns orchestration into a general-purpose service locator.

The recommended design is a narrow protocol-and-injection boundary: the foundation owns execution contracts and policy mediation points; platform subsystems own concrete capabilities; orchestration composes platform workflows and invokes registered agents; agents own specialist logic. This has some interface/wiring cost and requires automated rules, but it preserves ownership, optional integrations, independent tests, and a clear acyclic direction without adding unnecessary abstraction to simple agents.

## 9. Independently testable subsystems

Each major subsystem must define unit tests for its own logic and contract tests for public interfaces. Integration tests verify permitted interactions. End-to-end tests verify complete user workflows. Verification and evaluation are distinct: verification assesses a repository or proposed change; evaluation measures CodeWise itself.

## 10. Decisions intentionally deferred

The following choices require explicit architecture decisions before implementation:
- Graph database selection and whether a graph store is required for the first milestone.
- Vector database selection and embedding strategy.
- PostgreSQL schema and persistence boundaries; Redis use cases and deployment mode.
- Background job technology and WebSocket channel-layer deployment.
- Sandbox runtime and threat-model acceptance criteria.
- Exact provider list, free-quota tracking and local-model hardware requirements.
- Authentication provider and tenancy model.
- Frontend state-management library and testing toolchain.
- Deployment topology, secrets management, backup and disaster recovery.
- Agent registration bootstrap/discovery mechanism and the exact split of shared versus agent-specific configuration.
- Contract compatibility, version deprecation, and migration policy for agent and tool schemas.
- Exact internal module grouping and supported Pydantic, LangChain, and LangGraph versions.
- Concrete observability adapters, telemetry backend, retention policy, and cost/resource attribution details.
- Concrete retry budgets, idempotency conventions, and cancellation guarantees for each adapter/tool class.

Do not add technologies solely because they appear in this proposed tree. Select them when a documented requirement and trade-off justify them.

## 11. Non-goals of this skeleton

This structure does not initialize Django, install frontend dependencies, create React application files, create agent implementation modules, configure a database, start containers or connect paid providers. Those are later, separately approved implementation tasks.

## Repository workflow and GitHub boundaries

The user journey, grouped agent-selection contract, repository snapshot lifecycle, GitHub authorization boundaries, proposed versioned APIs, and patch publication protocol are specified in [repository-workflow.md](architecture/repository-workflow.md). That document supplements this canonical ownership architecture and does not authorize implementation.

GitHub provider access, commit/ref resolution, archive safety, storage lifecycle, retry behavior, and API integration proposals are detailed in [github-integration.md](architecture/github-integration.md); [ADR-002](architecture/adr-002-github-source-acquisition.md) records the commit-pinned acquisition decision. These documents preserve the existing repository-domain and agent boundaries and do not authorize OAuth or ingestion implementation.
