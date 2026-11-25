# ADR-001: Physical Package Location for the Shared Agent Foundation

- **Status:** Accepted
- **Decision date:** 2026-11-23
- **Decision scope:** Source package boundary and ownership only; no implementation or directory creation is included.

> **Implementation amendment (2026-11-24):** Task 7A places the runtime package at
> `backend/agent_foundation/`, where the Django backend and repository authorization
> services live. This refines the original top-level placement to the backend-owned
> location required by the implemented architecture. `agent_foundation` remains a
> distinct platform package, not a Django app, specialist agent, or orchestration
> subsystem. Task 7B places specialist directories under `backend/agents/`; Agent 1
> is deterministically registered through the foundation. The implementation and current integration limits are documented in
> [agent-foundation.md](agent-foundation.md).

## 1. Context and architectural problem

CodeWise V2 defines exactly eight specialist agents directly under `agents/`. They share execution concerns such as invocation contracts, validation, context, evidence, tool mediation, policies, errors, testing, and observability, while retaining distinct specialist reasoning and internal components. The architecture document calls this logical capability the Shared Agent Foundation and explicitly says it must not become a ninth agent or a monolithic base class.

The existing architecture distinguishes `shared/`, which is restricted to stable cross-subsystem contracts and small primitives, from `orchestration/`, which owns platform workflow composition, state, routing, checkpointing, recovery, and approval. The foundation needs executable, reusable infrastructure and must integrate with those boundaries without taking over either responsibility.

This ADR selects a physical package location and does not assert that the package, code, or directory currently exists. The canonical architecture document, `docs/architecture.md`, now reflects this accepted decision and describes `agent_foundation/` as shared execution infrastructure; `agents/` retain specialist implementations, `shared/` owns cross-subsystem contracts and types, `orchestration/` owns platform workflow coordination, and `llm_gateway/` owns centralized model and provider access. The package remains architecturally approved but not implemented.

## 2. Requirements and constraints

The selected location must:

- Make ownership of shared agent execution infrastructure clear and separate from specialist intelligence.
- Preserve the eight official agent directory names and avoid presenting the foundation as another agent.
- Support deterministic Agent 1 without requiring an LLM and meaningful LLM reasoning for Agents 2 through 8 via `llm_gateway`.
- Support optional LangChain integrations and agent-local LangGraph workflows without requiring either in every agent.
- Keep platform-level LangGraph orchestration in `orchestration/`.
- Avoid circular dependencies, duplicated shared contracts, and a monolithic inheritance model.
- Support validation, evidence references, tool permissions, controlled sandbox execution, testing, and observability through narrow interfaces.
- Avoid deciding implementation details that the architecture has deferred, including package/runtime choices, deployment topology, and provider selection.

## 3. Options considered

### Option 1: `agents/foundation/`

**Advantages**

- Places agent-facing execution support near its consumers.
- Makes the relationship to the agent family visible.
- Could be convenient for a small internal helper package.

**Disadvantages**

- `agents/` is documented as containing exactly the eight specialist agents. A sibling `foundation/` directory risks being mistaken for a ninth agent or part of the canonical agent roster.
- Blurs the boundary between shared platform infrastructure and agent-owned specialist implementations.
- Makes it less clear that non-agent callers, such as orchestration adapters and platform observability integrations, may use its stable invocation interfaces.

### Option 2: `shared/agent_foundation/`

**Advantages**

- Signals that the capability is reused across agents.
- Is close to the existing shared contracts and protocols.

**Disadvantages**

- Conflicts with the documented boundary for `shared/`, which contains stable cross-subsystem contracts and small primitives, not executable agent lifecycle services.
- Risks turning `shared/` into a general-purpose code bucket and coupling stable data contracts to evolving execution policy, tools, framework adapters, and runtime behavior.
- Makes the foundation's agent-execution ownership less distinct from the cross-subsystem contract layer.

### Option 3: top-level `agent_foundation/`

**Advantages**

- Gives shared agent execution infrastructure a clear, first-class owner separate from `agents/`, `shared/`, and `orchestration/`.
- Cannot be confused with one of the eight numbered specialist agent directories because it is outside `agents/` and is explicitly a platform capability.
- Allows the package to evolve its runtime services and optional framework/platform adapters without putting executable infrastructure into the stable contract layer.
- Provides a natural boundary for focused tests, validation, tool mediation, and execution observability.

**Disadvantages**

- Adds a new top-level subsystem to the proposed tree and requires a later update to `docs/architecture.md` and ownership guidance.
- Requires explicit dependency rules so it does not become a catch-all for orchestration, platform clients, or agent-specific functionality.
- May be more structure than needed if the eventual implementation is extremely small; package contents should remain cohesive and minimal.

### Option 4: place it under `orchestration/` or another existing subsystem

**Advantages**

- Reuses an existing top-level package and avoids adding a new top-level directory.

**Disadvantages**

- `orchestration/` owns workflow composition and control, not reusable agent execution infrastructure. Placing the foundation there would couple individual agent invocation to platform graph execution and confuse lifecycle control with workflow control.
- Other existing subsystems have narrower ownership: `llm_gateway/` owns model access, `sandbox/` owns isolated controlled execution, `verification/` owns verification capabilities, and `shared/` owns contracts and primitives. None is the appropriate owner for the cross-cutting agent lifecycle as a whole.

## 4. Dependency and ownership implications

The foundation is a reusable platform library/package at `agent_foundation/`, not an agent, agent registry entry, orchestration graph, or domain service. Its public surface should consist of narrow invocation/lifecycle protocols and composable services, not a mandatory base class.

The intended dependency direction is:

1. Specialist agent packages depend on the foundation's stable execution interfaces and on versioned contracts in `shared/contracts/` where those contracts cross subsystem boundaries.
2. The foundation may depend on stable shared contracts and small shared primitives. It must not import private modules from any specialist agent.
3. Integration adapters for observability, `llm_gateway`, repository/intelligence services, and sandbox/verification capabilities should use explicit interfaces and dependency injection. The foundation must not reimplement or take ownership of those subsystems.
4. Agents 2–8 obtain model access through `llm_gateway`. LangChain model adapters, when used, must route through that gateway. Agent 1 can use the same lifecycle, validation, evidence, and observability services without loading or invoking an LLM integration.
5. Agent implementations expose the agreed invocation contract to platform orchestration. `orchestration/` owns cross-agent routing, workflow state, checkpoint/recovery policy, and human approval; the foundation does not own or import platform graph implementations.
6. Controlled commands, tests, builds, and other execution of repository content remain behind authorization and the `sandbox/` boundary. The foundation mediates declared tool capabilities but does not itself execute untrusted repository content.

Agent-private schemas, prompts, tools, specialist reasoning, and semantic result interpretation remain owned by each agent. Cross-agent communication uses versioned shared contracts and orchestration, never direct imports between agents. Foundation code must not duplicate contract definitions already owned by `shared/contracts/` or domain validation owned by an agent.

## 5. Recommended option

**Choose Option 3: a dedicated top-level `agent_foundation/` package.**

The package location is reserved by this decision for shared, executable agent lifecycle and cross-cutting execution infrastructure. The decision does not create `agent_foundation/` or prescribe its internal modules. Its scope should stay limited to common execution plumbing: invocation contracts/adapters, context propagation, generic validation hooks, capability/tool mediation interfaces, lifecycle/error handling, and observability integration points.

## 6. Reasons for the recommendation

A dedicated top-level package best matches the nature of the foundation: it is executable platform infrastructure shared by agents, not specialist intelligence, stable data-only contracts, or end-to-end workflow orchestration. This gives it a clear owner and dependency boundary while preserving the existing responsibilities of `agents/`, `shared/`, `orchestration/`, `llm_gateway/`, `sandbox/`, and `verification/`.

Its location also avoids adding a ninth entry under the official agent roster. It supports both execution modes: deterministic agents can consume the common lifecycle without LLM setup, while Agents 2–8 use gateway-backed reasoning through optional adapters. Framework use remains selective: LangChain can provide model/prompt/structured-output interfaces, and LangGraph can implement stateful local or platform workflows in their respective ownership boundaries.

## 7. Consequences and trade-offs

- The canonical architecture document was subsequently updated to include the accepted top-level package placement and its ownership boundaries.
- The foundation becomes a separately testable subsystem with explicit public interfaces and ownership rules.
- Contributors must keep the package cohesive and resist adding specialist business logic, general platform clients, orchestration graphs, or duplicate shared types to it.
- Optional integrations should be isolated behind interfaces so deterministic execution does not import or require provider configuration, and tests can substitute controlled adapters.
- Dependency inversion and narrow protocols add some interface design work, but reduce circular imports and allow platform adapters to evolve independently.
- This ADR selects a source package boundary only. It does not decide whether the foundation runs in-process or as a service, which Python/framework versions or package manager are used, how it is distributed, or how deployment and persistence work.

## 8. Alternatives rejected and why

- **`agents/foundation/`:** rejected because it is too easy to interpret as a ninth agent and makes shared execution infrastructure appear owned by the specialist-agent collection.
- **`shared/agent_foundation/`:** rejected because it violates the deliberately narrow role of `shared/` and couples stable contracts to executable runtime policy and framework integrations.
- **`orchestration/` or another existing subsystem:** rejected because those packages have distinct owners and responsibilities; placing the foundation there would conflate agent execution with workflow composition or overload a narrower subsystem.

## 9. Future considerations

Before implementation, follow the placement and ownership boundaries now documented in `docs/architecture.md`. An implementation plan should define the minimal public protocols, the boundary between foundation services and adapters, and the test strategy without creating a monolithic base class.

The following details remain open and must not be inferred from this package-placement decision:

- Exact public interface shapes, package modules, and whether any service is separated into a process.
- Pydantic and LangChain/LangGraph versions and which agents, if any, adopt each framework internally.
- The precise division of common validation hooks versus agent-owned semantic validation.
- How invocation, tool, tracing, and metrics interfaces map to selected observability and policy implementations.
- Whether tool registration is static, configured, or dynamically discovered, and the precise authorization policy model.
- Checkpoint storage, retention, and recovery details for platform workflows.
- Provider/model selection, quotas, local-model support, and any paid API authorization remain governed by `llm_gateway` decisions and policy.
- The separate choice of when controlled execution is enabled and the accepted sandbox threat model.
