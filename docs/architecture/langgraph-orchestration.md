# Shared LangGraph Orchestration

## Workflow shape

`backend/orchestration/` compiles one native LangGraph `StateGraph` for all official agents. The graph uses a typed state and real LangGraph nodes, barriers, and interrupt support. Registered agent nodes call `ExecutionLifecycle`; they do not invoke runners directly. Node bindings are trusted server-side factories and are never serialized into graph state.

The mandatory agents are `structure_analyzer` and `business_logic_analyst`. All three optional groups are selected by default:

| Group | Agents |
| --- | --- |
| `code_quality_security_planning` | `code_quality_analyst`, `security_analyst`, `migration_planner` |
| `patch_engineering` | `patch_author`, `test_engineer` |
| `final_review` | `review_agent` |

Agent 1 runs first, followed by Agent 2 after Agent 1 completes. Group A runs after both mandatory results complete. Patch author runs after selected Group A agents complete; test engineer follows patch author; final review follows all selected earlier agents. An incomplete dependency produces a skipped result with a safe dependency error. Optional groups can be disabled in `WorkflowRequest`; the mandatory pair cannot be disabled.

Only registered official implementations get graph nodes. Missing implementations are reported as `unavailable`; they are never replaced with placeholder agents. Agents 1 and 2 are mandatory; Agent 3 is registered in the optional `code_quality_security_planning` group and runs only when that group is selected. Agent 3 independently analyzes the snapshot and does not copy or depend on Agent 2's interpretations. Agents 4–8 remain unavailable until their specialist implementations are registered (dependent stages are skipped).

The workflow passes prior bounded JSON outputs to the next trusted input factory. Repository access remains in each agent's `ExecutionContext` and authorized repository reader. Graph state contains snapshot identifiers/digests, selected IDs, statuses, bounded agent results, safe error codes, trace events, and approval state. Pool credential values are resolved only during gateway/provider invocation, redacted from provider output, and are not returned to agent runners; pool configuration and resolvers are not placed in the workflow state. Do not place credentials, raw repository source, prompts, filesystem paths, or provider clients in state. Future agent output contracts must use evidence references rather than source excerpts. State results are capped at 1 MB per agent.

## Checkpoints, approval, and recovery

`build_workflow` compiles without a saver by default. `InMemorySaver` is exposed for local development and tests only; it is process memory, is lost at restart, and is not durable recovery. No database checkpoint backend is included. A production saver must be separately selected and reviewed for encryption at rest, access control, retention/deletion, serialization, and handling of sensitive agent output before it is connected.

When `approval_required=True`, and both a registered patch author and final reviewer complete, the graph pauses at a LangGraph `interrupt`. `run_workflow` requires an explicitly configured saver and stable `thread_id`; `resume_workflow` accepts only `approved` or `rejected`. If either agent is unavailable, not selected, or did not complete, approval is marked `not_applicable`. Approval records a human decision only. It does not grant `patch.apply` or `github.publish`, and the workflow contains no patch-application or publishing node. Sandbox execution is not implemented here.

Agent executions are deterministic UUID5 children of the workflow execution ID, making node retries address the same agent execution ID. The graph itself does not automatically retry failed agent nodes. LLM retries, when later configured, are separately controlled by the gateway's idempotency policy. Cancellation and deadlines are checked by `ExecutionLifecycle` at the node boundary and by each agent's context during work.

## Observability and limits

Events contain stage/agent IDs, execution IDs, status, trace ID, selected/registered counts, and safe error codes. They do not contain prompts, source text, tool arguments, credentials, or provider exceptions. Observer failures do not change workflow outcomes.

The graph is native LangGraph orchestration, but the current integration supplies no durable persistence, distributed queue, cross-process cancellation, or automatic recovery daemon. The gateway's pool budgets, concurrency and shared quota-bucket ledger are also process-local. This package does not claim those deployment features. See [LLM Credential Pools](llm-credential-pools.md) for implemented local isolation and production integration limits.

Tests cover native graph compilation and execution, dependency gating, missing-agent reporting, Agent 1's real lifecycle integration, optional-group selection, and interrupt/resume with `InMemorySaver`. No provider request is made.
