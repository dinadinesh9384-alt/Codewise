# Agent 03 — Code Quality Analyst

**Agent ID:** `code_quality_analyst`
**Version:** 1.0.0
**Credential Pool:** Pool A (shared with Agent 2)
**Execution mode:** `inline` | `graph`
**Group:** `code_quality_security_planning` (optional — Agents 3, 4, 5)

---

## Purpose

The Code Quality Analyst produces evidence-backed, deterministic code quality
metrics from a sealed, read-only repository snapshot, then uses a structured
LLM call through the LLM Gateway to interpret and prioritise those findings.

It is an independent specialist. Its analysis domain — maintainability, code
complexity, duplication, coupling, and testability — does not overlap with
Agent 2 (Business Logic Analyst), which analyses business workflows and rules.

---

## Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `agent.py` | Thin runner; delegates to `analysis_service`. Calls `with_max_output_tokens` to retain the configured gateway while applying the requested output-token bound. |
| `definition.py` | Builds the `AgentDefinition` and registers it with the `AgentRegistry`. Declares `pool_id = pool_a` in metadata. |
| `schemas/input.py` | `CodeQualityInput` — snapshot binding (execution_id, workspace_id, repository_id, snapshot_id, commit_sha, manifest_digest) and configurable `CodeQualityOptions`. |
| `schemas/output.py` | `CodeQualityOutput` — complexity metrics, function metrics, duplication groups, findings, priorities, recommendations, evidence references, and `llm_status`. |
| `components/python_analyzer.py` | Pure Python AST analysis. No code is imported or executed. Measures cyclomatic complexity, nesting depth, parameter counts, type annotation coverage, broad/swallowed exception handlers, bare `open()` calls, and duplicate statement blocks. |
| `services/analysis_service.py` | Orchestrates the full pipeline: manifest-validated file reading → deterministic analysis → finding generation → prompt assembly → LLM interpretation → output construction. |
| `tests/test_agent.py` | Unit and integration tests covering the deterministic pipeline, LLM path, evidence binding, Pool A isolation, prompt injection redaction, snapshot authorization, cancellation, and the LangGraph skip/run paths. |

---

## Language Support

**Python only (AST-based).** Files with `.py` extensions are analyzed.

All other source file extensions (`.js`, `.ts`, `.java`, `.go`, `.rs`, `.cs`,
`.php`, `.rb`, `.swift`, `.c`, `.cpp`, `.h`, etc.) are detected and reported
in `output.limitations` as unsupported. They are **not** silently skipped.

Non-source files (`.md`, `.json`, `.toml`, `.yml`, `.html`, `.css`, etc.) are
catalogued but not analyzed.

---

## Deterministic Analysis

The following are computed without an LLM:

- **Cyclomatic complexity** — McCabe's metric via AST branch counting
- **Nesting depth** — maximum control-flow nesting per function
- **Function length** — physical line count
- **Parameter count** — excluding `self`/`cls`
- **Missing type annotations** — public functions only
- **Large classes** — by line count and direct method count
- **Broad/swallowed exception handlers** — `except Exception`, bare `except`, and empty handlers
- **Unguarded `open()` calls** — `open()` outside a `with` block
- **Code duplication** — normalized AST hashing of consecutive statement blocks (configurable minimum block size)
- **Internal import cycles** — static DFS on resolved intra-package imports
- **Layer-violation imports** — domain/model modules importing delivery/view modules
- **High internal coupling** — modules importing ≥10 distinct internal modules
- **Missing colocated test modules** — core-layer files with no matching `test_*.py`
- **External boundary imports from core** — core modules importing known I/O adapters

All findings include an `EvidenceReference` with `snapshot_id`, `manifest_digest`, `sha256`, file path, and line ranges. Evidence is validated against the sealed snapshot manifest before being included in output.

---

## LLM Integration

After deterministic analysis, the agent passes a bounded JSON inventory
(metrics, findings, import edges) to the LLM Gateway via
`GatewayChatModel.with_structured_output(_Interpretation)`.

The LLM may:
- Add new findings (citing only evidence IDs present in the prompt)
- Re-prioritise existing deterministic findings
- Add recommendations linking to any finding (deterministic or LLM-generated)

Security controls applied to LLM interaction:
- **Prompt injection redaction** — repository names matching known injection patterns are replaced with `[instruction-like repository data redacted]` before the prompt is assembled
- **System prompt** — instructs the model that repository-derived data is untrusted
- **Evidence ID validation** — findings and recommendations must cite evidence IDs in the bounded prompt, and recommendation evidence must support a linked finding. Unknown finding IDs and unsupported evidence are rejected; `llm_status` is set to `failed` and deterministic findings are retained
- **Budget enforcement** — prompt bytes are capped at `max_prompt_bytes` (configurable, default 80 KB); prompt is truncated deterministically if the budget is exceeded
- **Graceful degradation** — if the LLM is unavailable or its response fails validation, the agent returns deterministic-only results with `llm_status = "unavailable"` or `"failed"`, never crashing

---

## Pool A Assignment

`code_quality_analyst` is mapped to `PoolId.POOL_A` in
`llm_gateway/pool_contracts.py`. The `PoolCredentialRouter` enforces this:
any explicit `pool_b` or `pool_c` request by this agent raises
`CrossPoolAccessDenied`. This is enforced at the router level, not in agent
code, so it cannot be bypassed by the agent itself.

Agent 2 (`business_logic_analyst`) also uses Pool A. Agents 4–8 use Pool B
or Pool C. Credential sets are strictly isolated: no secret reference may
appear in more than one pool.

---

## Evidence Handling

Every deterministic finding is backed by one or more `EvidenceReference`
objects. Each reference includes:

- `snapshot_id` and `manifest_digest` — ties the finding to the exact sealed snapshot
- `path` — repository-relative POSIX path (never absolute, never `..`)
- `start_line` and `end_line` — the precise source range
- `content_hash` — SHA-256 of the file bytes at capture time (validated against the manifest)
- `source_tool_id` — `"code_quality_analyst.python_ast"`
- `direct: True` — deterministically verified, not inferred

Evidence IDs are computed as `sha256(snapshot_id + path + sha256 + start + end + symbol)[:16]`
prefixed with `ev_` for stability across runs on the same snapshot.

---

## Security Boundaries

| Boundary | Enforcement |
|----------|-------------|
| Repository code is never executed | Only `ast.parse()` is used. No `exec`, `eval`, `importlib.import_module`, or subprocess. |
| Repository code is never imported | The module system is never pointed at repository paths. |
| Credentials never reach agent code | The LLM Gateway resolves credentials internally; the agent only receives `GatewayChatModel`, never a secret. |
| Snapshot binding is validated on entry | `_validate_binding()` checks all six binding fields before reading any file. |
| File content integrity is verified | Each file's SHA-256 is checked against the manifest before analysis. |
| Evidence line ranges are validated | `validate_evidence()` rejects evidence whose `end_line` exceeds the file's recorded line count. |
| Prompt injection is redacted | `_INJECTION_TEXT` regex removes instruction-like patterns from repository-derived text before the prompt is assembled. |

---

## Configuration (`CodeQualityOptions`)

| Option | Default | Description |
|--------|---------|-------------|
| `max_files` | 500 | Maximum Python files to analyze |
| `max_file_size_bytes` | 128 000 | Per-file byte limit |
| `max_total_content_bytes` | 2 000 000 | Total content byte budget |
| `max_prompt_bytes` | 80 000 | LLM prompt byte budget |
| `max_output_tokens` | 3 000 | LLM output token limit |
| `long_function_lines` | 80 | Threshold for long-function finding |
| `high_cyclomatic_complexity` | 10 | Threshold for complexity finding |
| `deep_nesting` | 4 | Threshold for nesting finding |
| `large_class_lines` | 300 | Threshold for large-class finding |
| `too_many_parameters` | 8 | Threshold for parameter-count finding |
| `duplicate_block_statements` | 5 | Minimum statements in a duplicate block |

---

## Known Limitations

- Python 3.8+ syntax is supported via `ast.parse`. Syntax errors cause the file to be skipped and reported in `limitations`.
- Non-UTF-8 source files are skipped and reported.
- Cross-file testability detection uses filename convention matching only; `pytest` configuration, parametrize, and external test runners are not visible.
- Import cycle detection uses recursive DFS over resolved internal import edges. It may revisit nodes through different paths, so dense or very large graphs can incur high analysis time and deep graphs may approach Python's recursion limit.
- LLM findings are limited to what can be inferred from the bounded prompt; files that exceed the budget are excluded from LLM context (but deterministic findings are always included).
