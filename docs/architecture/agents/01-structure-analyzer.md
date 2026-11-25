# Agent 1: Structure Analyzer

## Responsibility and registration

The deterministic specialist has stable ID `structure_analyzer`, display name **Structure Analyzer**, and version `1.0.0`. Its implementation is in `backend/agents/01_structure_analyzer/`. `agents.registry.register_structure_analyzer` registers one fixed, trusted module definition with the Task 7A `AgentRegistry`; it does not scan directories or import repository-controlled modules. The definition is executed by `ExecutionLifecycle` with the foundation's Pydantic validation, context binding, permission checks, result envelope, and observability hook.

The agent declares `analysis.run` and `repository.read`. The caller injects an authorized `RepositoryReader`; the agent does not receive credentials, storage clients, paths, or a service locator. It imports no model, LangChain, LangGraph, or provider integration and makes no LLM calls.

## Input and output

`StructureAnalysisInput` binds the execution, workspace, repository, sealed snapshot, full commit SHA, and manifest digest. `AnalysisOptions` has hard bounded defaults: 2,000 inspectable files, 256,000 bytes per content read, 5,000,000 total content bytes, and depth 32. Absolute maxima are 20,000 files, 2,000,000 bytes per file, 20,000,000 total content bytes, and depth 64. Options also control framework/dependency/entry-point detection.

`StructureAnalysisOutput` includes execution and snapshot metadata, a deterministic tree, repository totals, maximum depth, largest files, extension and size distributions, language statistics, technology evidence, configuration inventory, dependency manifests, entry-point candidates, structural observations, findings, evidence references, and analysis completeness/omissions. It includes no analysis timestamp or generated random ID. Repeating an execution over the same snapshot/options yields stable analysis content; the execution ID remains explicit metadata.

## Snapshot and evidence flow

The foundation's `SnapshotRepositoryReader` verifies workspace authorization, sealed status, repository/commit binding, manifest digest and file integrity. The agent's `RepositoryInspector` accepts only safe relative paths, requires `repository.read`, and reads through that interface. Manifest metadata is enumerated once and sorted; content is read only for supported dependency manifests and extensionless files where a shebang may identify the language. Source files are classified by path/extension and are not read or executed. Files are never imported, evaluated, passed to a shell, or installed.

Evidence references cite repository-relative manifest paths and bind snapshot ID, manifest digest, and per-file content hash. Language, framework, configuration, entry-point, and structural observations cite relevant paths. Findings use the shared Foundation finding type and `evidence_supported`; they do not claim business-logic correctness or runtime verification.

## Detection methods

Language classification uses recognized extensions and safe shebang inspection only where bounded content is available. The supported extension set covers Python, JavaScript, TypeScript, Java, C, C++, C#, Go, Rust, PHP, Ruby, Swift, Kotlin, Dart, HTML, CSS, SCSS, SQL, shell, PowerShell, R, Scala, Lua, Vue, and Svelte. Ambiguous/unknown extensions (including `.h`) remain `Unknown`. Counts and byte totals are based on all manifest file metadata; shebang detection can be unavailable when content limits apply.

Framework signals require dependency declarations or a relevant configuration/component path. Supported deterministic evidence includes Django, Flask, FastAPI, Pyramid; React, Next.js, Vue, Nuxt, Angular, Express, NestJS, Vite; Spring Boot, ASP.NET Core, Laravel, Rails, Symfony, Gin, Fiber, Actix and Axum; plus Docker, Docker Compose, Kubernetes-style manifests, Terraform, GitHub Actions, Jenkins, Nginx and Apache configuration. This is evidence-based pattern matching, not complete framework discovery.

Configuration inventory is path/name based and covers common Python, Node/TypeScript, Java, Rust, Go, PHP, Ruby, .NET, Docker, CI, web-server, environment-template and Terraform files. Secret-like paths (`.env`, credential/secret names, key/certificate stores, `.tfvars`) are listed with metadata only and content is intentionally skipped.

Dependency declarations are parsed without package resolution for `requirements.txt`, `pyproject.toml`, Pipfile, `package.json`, Maven/Gradle, Cargo, `go.mod`, Composer, Gemfile and `.csproj`. Constraints/groups are direct declarations; they do not mean a package is installed or resolved. JSON/TOML are parsed with standard-library parsers. XML DTD/entity declarations are rejected before parsing. URL/credential-shaped and otherwise unsafe declaration values are omitted. Malformed or over-budget manifests are surfaced as parse status/limitations. No package manager, network, build tool or project script is invoked.

Entry points are classified as confirmed only when an existing `package.json` main declaration names an existing file; other common filenames are labelled conventional or weak candidates. No entry point is executed.

## Limits, determinism and known gaps

The sealed manifest currently contains file records, not directory records. Directory nodes/counts are synthesized from file paths; explicit empty directories cannot be reported. The full path inventory and metadata statistics remain available under the file-count option, while content inspection is limited. Omitted reads include a reason and make the result partial. A too-deep subtree is omitted from the emitted tree but remains represented in manifest-wide file/language/size totals.

Language/framework detection is intentionally heuristic and cannot establish runtime use, installed packages, architecture intent, or business logic. Some ecosystems and manifest dialects are unsupported; Gradle/Ruby parsing handles common line-oriented declarations, not arbitrary DSL. YAML is inventoried by path but not parsed. No semantic retrieval, AST graph, knowledge graph, secret scanning, line-level source analysis, code execution, or caching beyond one analysis invocation is implemented.

## Distinction from Agent 2

Agent 1 reports observable files, languages, configurations, declared dependencies, candidate entry points, and evidence-backed structural patterns. It does not infer feature behavior, domain rules, control-flow intent, or business requirements. Those belong to Agent 2.
