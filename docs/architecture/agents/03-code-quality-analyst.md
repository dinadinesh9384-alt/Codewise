# Code Quality Analyst

## Responsibility and integration

`code_quality_analyst` is Agent 3, registered by the trusted `agents.registry.register_available_agents` entry point. It is attached to the existing `code_quality_security_planning` optional group through `code_quality_analyst_binding`; it runs only when that group is selected and the mandatory Agent 1 and Agent 2 stages complete. The agent performs its own snapshot analysis and does not copy or depend on speculative Agent 2 interpretations.

The agent requires `analysis.run`, `repository.read`, and `llm.invoke`. The central gateway maps its stable ID to Pool A. Agent code accepts only the gateway-backed LangChain model and contains no provider SDK import. Its output is a strict Pydantic model and flows through `ExecutionLifecycle` in the shared LangGraph.

## Deterministic analysis

`components/python_analyzer.py` parses Python with the standard-library AST without importing or executing repository modules. It computes per-function cyclomatic complexity, nesting depth, source length, parameter count, and missing annotation counts; per-class size and direct method counts; broad and swallowed exception handlers; `open()` calls not lexically within a context manager; static imports and references; and exact normalized AST blocks repeated across files.

The service builds deterministic quality findings from configurable thresholds, aggregates internal import edges for circular-dependency and coupling candidates, detects selected layering inversions, and reports cautious test-evidence and external-boundary observations. It labels likely-unused private functions as uncertain because dynamic references and external callers cannot be ruled out. Import cycles are verified as static edges while runtime import consequences remain uncertain.

Python is the only analyzed source language. Other source languages, malformed files, and configured file/content limits appear in coverage limitations. Metrics and source locations are stable for a fixed snapshot and options; findings use stable hashes of snapshot evidence and measured candidates. Heuristics are deliberately conservative and do not classify style variation alone as a defect.

## LangChain reasoning

The model receives bounded metrics, candidate findings, and internal import edges rather than source files. Structured output can rank deterministic findings, connect evidence across files, add qualified architecture, maintainability, and testability findings, and propose recommendations. New claims must cite evidence IDs included in the prompt; recommendations must cite evidence linked to the findings they reference. Unknown references or invalid schemas reject the model interpretation and preserve deterministic findings with a partial outcome.

Repository-derived identifiers are passed as data under an explicit untrusted-data instruction; raw comments and source text are not sent. Prompt bytes and output tokens are bounded by input options, and the gateway enforces its own context and request policies. If the gateway is disabled or unavailable, the result reports `llm_status=unavailable` and retains deterministic findings.

## Output and evidence

The Pydantic output contains a quality overview, per-file complexity metrics, duplicate-block groups, standard Agent Foundation findings, priorities, linked maintainability/architecture/testability finding IDs, recommendations, uncertainties, language coverage, and evidence references. Every metric, deterministic finding, duplicate location, and model-added finding carries evidence tied to the execution snapshot and manifest digest, repository-relative path, content hash, source tool, and line range where available. `validate_evidence` checks the sealed manifest hash and line bounds.

## Security and limitations

Only the authorized `RepositoryReader` is used. Reads are bounded and checked against manifest SHA-256 values. Repository modules are never imported, executed, or installed. Repository contents are untrusted, no credentials enter context or prompts, and the agent cannot choose a credential pool. No live provider or paid call is needed for deterministic analysis.

Static metrics cannot establish runtime complexity, actual test coverage, or whether an architectural pattern is intentional. Exact AST duplication can miss semantically similar code and can flag intentional repetition. Language coverage, dynamic imports, dependency injection, decorators, reflection, and framework-specific runtime behavior remain limited.
