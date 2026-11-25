# Agent 4 — Security Analyst

**Stable ID:** `security_analyst`  
**Credential pool:** Pool B only  
**Execution modes:** `inline`, `graph`  
**Version:** `1.0.0`

Agent 4 analyzes authorized, immutable repository snapshots. It uses bounded deterministic Python AST and configuration checks first, then asks the shared LangChain `GatewayChatModel` to assess those findings, explain possible relationships, and recommend fixes. The model sees sanitized findings, evidence IDs, paths, and line ranges; it does not receive source text or credential literals.

## Deterministic checks

- Credential-shaped literals in Python and selected text configuration files. Values are never copied to findings or the LLM prompt; credential validity is not checked.
- Local request-input flow into dynamically constructed SQL, shell command, file path, dynamic-code, or HTML-safe-marking sinks.
- Potentially unsafe `pickle`, `dill`, `marshal`, and unsafe YAML deserialization.
- Explicit `verify=False` TLS settings, literal `DEBUG = True`, and `csrf_exempt` decorators.
- Findings carry stable IDs, CWE classifications where applicable, severity, confidence, remediation, uncertainty, and snapshot-bound line evidence.

Request-to-sink flow analysis is deliberately local and conservative. It tracks simple assignments within a Python function; it does not model sanitizers, interprocedural flow, framework middleware, runtime authorization, or dynamic dispatch. Candidate findings therefore remain evidence-supported risks, not confirmed exploitable vulnerabilities.

## LLM reasoning and validation

Structured output provides per-finding assessments, evidence-backed attack-path or missing-control insights, and recommendations. The service rejects unknown finding IDs, evidence outside the bounded prompt, and evidence that does not support a linked finding. The LLM cannot add file paths or line numbers and cannot promote a finding's deterministic verification status. Unavailable or malformed model responses retain deterministic results and report the LLM status.

`security_analyst` is assigned to Pool B by the shared gateway mapping. Calls use the configured Pool B router and its isolated credentials. There is no provider SDK, direct network path, or advisory-database lookup in this agent.

## Scope and limitations

- Python is the only source language with AST and local flow analysis. Selected dotenv/INI/TOML/YAML/JSON/properties files receive credential-pattern checks only.
- Dependency names and versions are not matched against advisories. No network vulnerability database is queried.
- Authentication, authorization, session, upload, cryptographic, and framework security behavior are not comprehensively analyzed.
- Static analysis cannot prove that code is exploitable or that a repository is free of vulnerabilities. Findings and LLM insights retain explicit uncertainty.
- File count, per-file bytes, total content bytes, prompt bytes, and output tokens are bounded by `SecurityAnalysisOptions`.

Repository code is treated as untrusted data. The agent never imports, executes, installs, or runs repository content. Snapshot identity and SHA-256 integrity are checked before evidence is emitted.
