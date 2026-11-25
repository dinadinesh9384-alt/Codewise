# Business Logic Analyst

## Responsibility and integration

`business_logic_analyst` is Agent 2, registered explicitly through `backend/agents/02_business_logic_analyst/definition.py` and the trusted `agents.registry.register_available_agents` composition entry point. It consumes the same execution context and authorized sealed-snapshot reader used by Agent Foundation. Its contract requires `analysis.run`, `repository.read`, and `llm.invoke`; the gateway's fixed agent map routes it to Pool A. It has no provider SDK imports and cannot choose credential pools or credentials. A disabled or unavailable gateway produces a partial deterministic result with an explicit `llm_status`, never a fabricated model interpretation.

The agent runs as a node in the shared LangGraph workflow after Agent 1, through `ExecutionLifecycle`. It does not create a graph of its own. Agent 1 remains deterministic and LLM-free. No provider configuration is added by this agent.

## Deterministic analysis

The service lists authorized manifest metadata, selects bounded Python source files, verifies each read against its manifest SHA-256, then parses source with Python's standard-library AST. It extracts top-level functions/classes, class fields, imports, calls, route-like declarations, and conditional expressions without importing or executing repository modules. Entity-shaped classes, route candidates, repeated symbol names, and business-like condition candidates are emitted as deterministic observations. Conditions are explicitly hypotheses until contextual evidence supports a business meaning.

Python is currently the only language with AST-based business analysis. Other source-language extensions are reported as coverage limitations. Malformed Python is skipped with a limitation. File count, per-file bytes, total bytes, prompt bytes, and output tokens are bounded by the input contract. Parsing does not follow imports, execute tasks, or prove reachability.

## LLM reasoning boundary

The lifecycle supplies only `GatewayChatModel`; structured interpretation uses its LangChain structured-output runnable, so all requests pass through the central gateway and its Pool A assignment. The prompt contains deterministic AST facts and evidence IDs rather than repository source wholesale. Repository content remains untrusted data; the system instruction prohibits treating it as instructions. The model must cite only supplied evidence IDs, and the agent rejects results containing unknown IDs. Findings retain evidence references and uncertainty labels.

The model connects likely workflows, business rules, entities and relationships, execution paths, shared logic, cross-module dependencies, duplication candidates, contradictions, and missing information. These are evidence-supported interpretations, not deterministic proofs. If interpretation fails validation or the gateway is unavailable, only deterministic findings are returned and coverage is marked partial.

## Evidence and output

Evidence binds a stable ID to snapshot UUID, manifest digest, repository-relative file path, observed line range where nonempty, file SHA-256, and the AST inventory source tool ID. The foundation validator checks snapshot binding, path safety, hash, and available line bounds. Stable IDs are hashes of snapshot/path/content identity (or finding inputs); model-generated prose is deliberately not assigned deterministic semantic truth.

The strict Pydantic output includes executive/domain summaries, workflows, business rules, entities and relationships, execution paths, shared logic, dependencies, duplication candidates, contradictions, uncertainties, coverage, limitations, findings, and evidence references. Direct AST facts and inferred model claims carry distinct reasoning classifications.

## Security and limitations

The agent uses only `RepositoryReader` APIs and never accesses snapshot storage. It does not import repository modules, invoke shell commands, install dependencies, follow links, or receive credentials. Context contains identifiers and permissions only. Model transport and pool isolation are owned by Agent Foundation and `llm_gateway`.

Current limitations include Python-only semantic inventory, no dynamic call graph or runtime reachability analysis, conservative route and entity heuristics, and model interpretation that depends on configured gateway access. No production provider call or provider configuration is part of implementation or validation.
