# Authenticated API v1

All endpoints below require an authenticated Django session. Session-authenticated unsafe methods require Django CSRF protection. The API does not accept workspace IDs, actor IDs, or execution identity claims from a trusted context; workspace and actor identity come from the URL plus authenticated membership.

## Endpoints

| Method and path | Permission | Behavior |
| --- | --- | --- |
| `GET /api/v1/health/` | Public | Process health only. |
| `POST /api/v1/workspaces/{workspace_id}/submissions/` | Workspace member | Submit a public GitHub repository URL and seal a pinned snapshot. Requires `Idempotency-Key`. |
| `GET /api/v1/workspaces/{workspace_id}/submissions/{submission_id}/` | Workspace member | Read submission and snapshot lifecycle. |
| `POST /api/v1/workspaces/{workspace_id}/executions/` | Workspace member | Start analysis of a sealed submission. Requires `Idempotency-Key`. |
| `GET /api/v1/workspaces/{workspace_id}/executions/{execution_id}/` | Workspace member | Read execution state, agent progress, and approval status. |
| `GET /api/v1/workspaces/{workspace_id}/executions/{execution_id}/agents/` | Workspace member | Read structured Agent 1–8 results, including errors, uncertainty, evidence, and provenance. |
| `GET /api/v1/workspaces/{workspace_id}/executions/{execution_id}/approval-proposals/` | Workspace member | Read pending proposals and Agent 8 review records. |
| `POST /api/v1/workspaces/{workspace_id}/executions/{execution_id}/approval/` | Workspace owner or admin | Approve or reject the complete pending proposal set. Body: `{"decision":"approved|rejected","proposal_ids":[...]}`. |

Submission body:

```json
{"repository_url":"https://github.com/owner/repository","ref":"main","ref_kind":"branch"}
```

Only canonical HTTPS `github.com/{owner}/{repository}` public repositories are supported. `ref_kind` is `branch`, `tag`, or `commit`; an omitted/empty `ref` resolves the repository's default branch. Private repositories are rejected. Private GitHub grants exist in the domain model, but no authenticated private GitHub provider is implemented.

Execution body:

```json
{"submission_id":"uuid","groups":["code_quality_security_planning","patch_engineering","final_review"],"approval_required":false}
```

The only accepted groups are `code_quality_security_planning`, `patch_engineering`, and `final_review`. Agents 1 and 2 remain mandatory. Group selection follows `WorkflowRequest.selected`; Agent 8 remains optional under `final_review`.

## Lifecycle and errors

Submission states are `resolving`, `awaiting_authorization`, `retrieving`, `validating`, `sealed`, `failed`, and `cancelled`. Execution states are `queued`, `running`, `pending_approval`, `completed`, `partial`, `failed`, `cancelled`, and `timed_out`. A `completed` execution is returned only when orchestration says completed and every selected agent has both completed status and a structured output. Missing/unavailable/skipped optional agents remain visible in agent results; they do not become successful results.

Responses use stable error codes such as `invalid_repository_url`, `submission_not_ready`, `execution_not_found`, `idempotency_key_conflict`, `workflow_runtime_unavailable`, `approval_not_pending`, and `approval_review_not_ready`. Error responses do not include exceptions, stack traces, or credentials. Object lookups are workspace-scoped, so foreign objects are not exposed.

Idempotency keys are mandatory for submission and execution creation, scoped to the workspace, and bound to a canonical request fingerprint. Replaying the same key and body returns the original object; replaying a key with another body returns HTTP 409. Concurrent creates are constrained by database uniqueness.

## Execution runtime and persistence limits

Repository submission uses the existing GitHub provider, archive validation, manifest verification, private local snapshot storage, and snapshot sealing service. It is synchronous and can take as long as GitHub retrieval and repository ingestion.

Workflow execution uses the existing LangGraph entry point through `CODEWISE_API_WORKFLOW_FACTORY`, which defaults to `config.workflow_runtime.build_api_workflow_runtime`. The composition loader requires explicit `CODEWISE_LLM_POLICY_CONFIG` and `CODEWISE_LLM_MODEL_CAPABILITIES` JSON, and loads `CODEWISE_LLM_POOL_A_CONFIG`, `CODEWISE_LLM_POOL_B_CONFIG`, and `CODEWISE_LLM_POOL_C_CONFIG` independently. Enabled pool credentials are resolved server-side from `CODEWISE_LLM_CREDENTIAL_<NAME>` references. Missing or invalid configuration fails closed as `workflow_runtime_unavailable`; no provider route is enabled merely because a credential exists. Execution remains synchronous. Configure provider routes and credentials only through the existing gateway; the API never accepts them.

Execution records, immutable snapshot bindings, result JSON, approval decisions, approval outbox commands, and audit events are database-backed. Workflow execution remains synchronous in the API process; there is no general worker queue, streaming progress, or cancellation endpoint. Approval commands are durably queued before checkpoint processing and processed inline; `python manage.py process_approval_outbox` retries interrupted or retryable approval operations.

Approval-resume persistence uses the pinned LangGraph Postgres checkpointer when `CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=true` and the configured Django database is PostgreSQL. After provisioning the application database, initialize its checkpoint tables with `python manage.py setup_langgraph_checkpoints`; the runtime then verifies the checkpoint schema and fails closed if it is unavailable. The checkpointer uses a strict serializer allowlist and the execution UUID as its stable thread ID. Without that explicit setting, a PostgreSQL database, and initialized checkpoint tables, approval-required starts and decisions fail closed with `durable_approval_checkpoint_unavailable`. A pending interrupt is bound to the execution's immutable snapshot and revalidated against stored Agent 6/8 outputs, proposal IDs, current snapshot identity, and upstream review provenance before resume. Checkpoint data contains workflow analysis state, so operators must protect the database, transport, backups, and retention policies. Credentials are not placed in graph state.

Human approval records the authenticated owner/admin, decision time, proposal identity, execution, and snapshot. The decision does not apply a patch or publish changes. AI readiness is not a human decision. No React frontend, Agent 1–8 behavior, pool architecture, or existing migration was changed.
