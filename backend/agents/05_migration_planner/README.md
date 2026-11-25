# Agent 5: Migration Planner

`migration_planner` produces a review-only migration plan from an authorized, immutable repository snapshot. It performs bounded deterministic inventory, then sends structured inventory and selected upstream findings to the shared LangChain gateway using **Pool B**. It never modifies files, imports repository modules, executes source, installs packages, or runs migrations.

The inventory identifies supported source languages, recognized build manifests, explicit dependency constraints, Python internal imports, database and API indicators, environment lookups (without values), migration/schema artifacts, and test-named paths. Exact versions are reported only for exact manifest constraints. Dependency advisories, runtime behavior, and unsupported manifest semantics are not inferred.

All repository facts and upstream findings retain snapshot and manifest-bound evidence. The model receives identifiers from this bounded evidence set. Output validation rejects unknown findings, evidence, paths, phases, tasks, invalid ordering, and dependency cycles. Caller-provided targets are explicitly labeled; recommendations remain evidence-backed. Plans are always marked unapproved and unexecuted, and a provider or validation failure retains deterministic inventory with an incomplete plan status.

In the LangGraph workflow, Agents 1 and 2 remain mandatory. Selected Agents 3 and 4 retain their parallel execution; Agent 5 runs after their join so it can consume their completed findings. It is skipped when unselected and is blocked when a selected upstream dependency does not complete.

## Limits

Defaults cap inventory to 600 files, 256 KB per file, 3 MB total content, 100 KB of prompt, 4,000 output tokens, and bounded finding/dependency counts. Secret-like `.env` files are excluded from reads. Language-specific analysis beyond Python imports is limited, and no external package database is used; a listed dependency is not labeled vulnerable or outdated by presence alone.
