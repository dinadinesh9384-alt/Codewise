# Shared Agent Foundation

## Status and package boundary

The shared foundation is implemented as `backend/agent_foundation/`. It standardizes registration, execution boundaries, typed inputs and outputs, scoped context, permission checks, tool mediation, evidence references, outcomes, and structured observability. It contains no specialist analysis. It is an in-memory execution library; it does not persist execution records.

The repository is independently structured from V1. Specialist directories under `backend/agents/` are explicitly registered and never scanned or imported dynamically by this package. The current V2 repository uses Pydantic 2.13.5 and Python 3.11. LangChain Core and LangGraph are pinned integration dependencies; they do not install or configure a model provider.

## Agent contracts and registry

`AgentDefinition` binds an explicit stable ID and descriptive `AgentContract` to Pydantic input/output model classes and a trusted runner. The official IDs are named strings; IDs are never inferred from directory names. Definitions capture model JSON Schemas, version, capabilities, required permissions, LLM requirement, supported modes, and metadata. The deterministic `structure_analyzer` is rejected if declared LLM dependent; the seven official reasoning agents must declare LLM dependency.

Registration is explicit via `register` or `discover` with a caller-supplied iterable of trusted definitions. No module scanning, dynamic imports, or plugin execution occurs. Duplicate IDs are rejected; `seal()` disables later mutation. Registry metadata exposes contracts, not runner internals.

## Execution lifecycle

`ExecutionLifecycle` resolves the ID, verifies agent version and request/context snapshot bindings, validates the input, checks requested and declared permissions, checks cancellation/deadline, transitions through `created -> validating -> queued -> running`, calls the runner, validates output, and returns a structured result. Terminal states are `completed`, `failed`, `cancelled`, and `timed_out`. Illegal transitions raise a typed safe error. LLM-required definitions fail closed unless a trusted chat-model factory supplies the gateway-backed LangChain model; those runners receive the model as a third argument. The lifecycle does not queue background jobs or persist state.

## Context and repository access

`ExecutionContext` carries principal/workspace/repository/snapshot identity, commit and manifest digests, agent/execution IDs, permission set, cancellation signal, deadline, and trace/correlation IDs. Credentials and filesystem paths are not context fields; construction rejects common credential/path injection keys. Context carries no repository bytes or service locator.

`RepositoryReader` is the provider-independent read protocol. `SnapshotRepositoryReader` connects to the existing repository app's `get_authorized_snapshot`, `read_snapshot_manifest`, and `read_snapshot_file` services. It requires the existing authenticated user and workspace membership, sealed snapshot state, repository binding, matching commit and manifest, verified manifest integrity, and bounded reads. It never returns a storage path. `list_paths`, metadata, and bounded literal text search are available. Semantic retrieval, symbol indexing, aggregate response-budget accounting across a batch search, and richer line-range APIs remain future work. The foundation does not directly access storage or execute repository code.

## Tools and permissions

`ToolRegistry` accepts explicit typed tool definitions only. Tool contracts declare schemas, stable ID, ownership/category, required permissions, timeout and output limit, mutability, and sandbox requirement. Invocation validates arguments/results, checks permissions and ownership, bounds runtime/output, and returns structured invocation metadata. Controlled-execution tools must declare a sandbox requirement and cannot run without a supplied sandbox executor. No general shell or Python execution tool exists. Python thread timeout cannot forcibly stop a running handler; sandbox executors must enforce hard isolation and termination for untrusted or mutating work.

The permission vocabulary is `repository.read`, `repository.search`, `analysis.run`, `evidence.read`, `llm.invoke`, `patch.propose`, `patch.apply`, `tests.execute`, `sandbox.execute`, and `github.publish`. Permissions are checked at execution and tool boundaries. Definitions do not grant permissions. Publishing remains out of scope; patch application and publishing are never automatically granted.

## Evidence, findings, and results

`EvidenceReference` binds a safe repository-relative path, optional line range and symbol, evidence type, source tool, content hash, snapshot ID, and manifest digest. `EvidenceFactory` creates file evidence from snapshot metadata; `validate_evidence` verifies binding, path, content hash and available line bounds. Evidence metadata is structured and does not embed source. `Finding` has controlled category/severity/verification enums, confidence bounds, evidence, uncertainty, and suggested action. An LLM confidence value does not change verification state; findings default to `unverified`. Evidence presence is traceability, not proof of semantic correctness.

`ExecutionResult` contains output, findings/evidence, invocation metadata, validation status, safe typed errors, timing, model usage, and correlation IDs. Results are returned in memory only.

## LangChain, LangGraph, and llm_gateway

`backend/llm_gateway/` is the single provider transport boundary. Its default policy has no enabled route or provider, and paid access is disabled. It supports the three explicit agent credential pools, while retaining the earlier single-route compatibility path. Agent 1 is excluded from gateway routing and cannot declare `llm.invoke`; lifecycle execution also rejects that permission in Agent 1 context. `GatewayChatModel` is a real LangChain Core `BaseChatModel`; prompt Runnables and structured output pass through the gateway. Credentials remain gateway/provider-boundary data and do not enter agent context or graph state. Pool settings, environment references, quota identity, fallback and paid gates are documented in [LLM Credential Pools](llm-credential-pools.md). Usage and cost remain unavailable when adapters cannot report them. Pool counters are process-local.

`backend/orchestration/` compiles a native LangGraph `StateGraph` and calls registered agents only through `ExecutionLifecycle`. Agents 1 and 2 are mandatory; the three configured optional groups default on. Agents 1, 2, and the Pool A code quality analyst are implemented; remaining official IDs are reported unavailable and dependent work is skipped. The graph supports an approval interrupt and injected checkpointers. No saver is enabled by default; the supplied `InMemorySaver` helper is for tests and local development, not durable recovery. No automatic graph retries, patch application, publishing, or sandbox execution is implemented. See [Business Logic Analyst](agents/02-business-logic-analyst.md), [Code Quality Analyst](agents/03-code-quality-analyst.md), [LLM Gateway](llm-gateway.md), and [LangGraph Orchestration](langgraph-orchestration.md) for policy and deployment boundaries.

## Errors and observability

Typed errors map failures to stable codes. Execution errors exposed to callers exclude arbitrary exception text and Pydantic input values. `ExecutionObserver` emits structured completion/tool events containing IDs, versions, state, duration, trace fields, error categories, and available model usage. It does not log prompts, source content, tool arguments/results, or credentials. A sink is injected; no monitoring backend/dashboard is included.

## Security boundaries and extension points

Workspace authorization remains owned by the repository snapshot services. Agents receive only reader interfaces and evidence references, never GitHub credentials, storage credentials, private storage paths, or raw filesystem handles. Tools remain explicit and least-privileged. Any future sandbox implementation must enforce process isolation, resource limits, and termination; this package only defines the injection boundary.

Agent 1 registers deterministic input/output models and a Python runner without invoking a model. Agent 2 registers bounded Python AST inventory and evidence-grounded interpretation through the gateway-backed LangChain model. Agent 3 registers deterministic Python quality metrics and evidence-grounded quality interpretation through the same gateway. Agents 4–8 declare an LLM requirement and can run only when trusted composition supplies the gateway-backed LangChain model; their specialist logic remains unimplemented. Provider adapters, durable checkpoint storage, semantic retrieval, knowledge graph, patch execution, and GitHub publishing are not implemented.
