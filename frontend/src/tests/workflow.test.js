import test from "node:test";
import assert from "node:assert/strict";
import { AGENT_GROUPS, buildWorkflowRequest, createRepositorySubmission, selectedAgentIds, startWorkflow, submitApprovalDecision, validateGitHubRepositoryUrl } from "../services/repositoryWorkflow.js";
import { buildExecutionAgents, getPendingApprovalEligibility, statusDisplay } from "../services/executionViewModel.js";

test("repository URL validation accepts only supported public GitHub URL shape", () => {
  assert.equal(validateGitHubRepositoryUrl("https://github.com/openai/example.git"), "");
  for (const url of ["", "http://github.com/a/b", "https://github.com.evil/a/b", "https://github.com:443/a/b", "https://github.com/a/b?x=1", "https://github.com/a//b", "https://github.com/a/../b"]) {
    assert.notEqual(validateGitHubRepositoryUrl(url), "", url);
  }
});

test("all stable optional groups default to selection and mandatory agents remain included", () => {
  assert.deepEqual(AGENT_GROUPS.map((item) => item.id), ["code_quality_security_planning", "patch_engineering", "final_review"]);
  assert.deepEqual(selectedAgentIds(AGENT_GROUPS.map((item) => item.id)), ["structure_analyzer", "business_logic_analyst", "code_quality_analyst", "security_analyst", "migration_planner", "patch_author", "test_engineer", "review_agent"]);
  assert.deepEqual(selectedAgentIds([]), ["structure_analyzer", "business_logic_analyst"]);
});

test("workflow initiation sends group ids and disallows invalid approval configuration", async () => {
  let request;
  const client = { post: async (...args) => { request = args; return { execution: { id: "run" } }; } };
  await startWorkflow({ workspaceId: "123e4567-e89b-42d3-a456-426614174000", submissionId: "sub", selectedGroups: ["patch_engineering", "final_review"], approvalRequired: true, idempotencyKey: "run-key", client });
  assert.equal(request[0], "/api/v1/workspaces/123e4567-e89b-42d3-a456-426614174000/executions/");
  assert.deepEqual(request[1], { submission_id: "sub", groups: ["patch_engineering", "final_review"], approval_required: true });
  assert.equal(request[2].idempotencyKey, "run-key");
  assert.throws(() => buildWorkflowRequest({ submissionId: "sub", selectedGroups: ["unknown"] }), /unknown/);
  assert.throws(() => buildWorkflowRequest({ submissionId: "sub", selectedGroups: [], approvalRequired: true }), /required/);
  assert.throws(() => createRepositorySubmission({ workspaceId: "bad", repositoryUrl: "https://github.com/a/b", client }), /workspace UUID/);
});

test("repository submission posts the validated public URL with its stable request key", async () => {
  let request;
  const client = { post: async (...args) => { request = args; return { submission: { id: "submission" } }; } };
  await createRepositorySubmission({ workspaceId: "123e4567-e89b-42d3-a456-426614174000", repositoryUrl: "https://github.com/openai/example", ref: "main", refKind: "branch", idempotencyKey: "submission-key", client });
  assert.equal(request[0], "/api/v1/workspaces/123e4567-e89b-42d3-a456-426614174000/submissions/");
  assert.deepEqual(request[1], { repository_url: "https://github.com/openai/example", ref: "main", ref_kind: "branch" });
  assert.equal(request[2].idempotencyKey, "submission-key");
});

test("agent result view model preserves partial, skipped, unselected and unavailable states", () => {
  const result = buildExecutionAgents({ selected_agents: ["structure_analyzer", "business_logic_analyst"], agent_statuses: [
    { agent_id: "structure_analyzer", status: "completed" }, { agent_id: "business_logic_analyst", status: "failed", error_codes: ["timeout"] }, { agent_id: "patch_author", status: "skipped" },
  ] }, { agents: [{ agent_id: "structure_analyzer", status: "completed", output: { findings: [{ title: "Finding" }] } }] });
  assert.equal(result.find((item) => item.id === "structure_analyzer").output.findings.length, 1);
  assert.equal(result.find((item) => item.id === "business_logic_analyst").status, "failed");
  assert.equal(result.find((item) => item.id === "patch_author").status, "skipped");
  assert.equal(result.find((item) => item.id === "review_agent").status, "unavailable");
  assert.equal(result.find((item) => item.id === "review_agent").selected, false);
  assert.equal(statusDisplay("partial").label, "Partial results");
  assert.equal(statusDisplay("not_performed").label, "Not performed");
  assert.equal(buildExecutionAgents({ selected_agents: [], agent_statuses: [{ agent_id: "review_agent", status: "not_performed" }] }, { agents: [] }).find((item) => item.id === "review_agent").status, "not_performed");
  assert.equal(buildExecutionAgents({ selected_agents: [], agent_statuses: [] }, { agents: [] }).every((item) => item.output === null), true);
});

test("approval controls need pending interrupt, ready review, and verified proposals", () => {
  const base = { status: "pending_approval", approval: "pending", final_review_status: "ready_with_warnings" };
  const good = [{ proposal_id: "p1", review: { validation_status: "verified_in_memory", applied: false } }];
  assert.equal(getPendingApprovalEligibility(base, good).eligible, true);
  assert.equal(getPendingApprovalEligibility({ ...base, status: "completed" }, good).eligible, false);
  assert.equal(getPendingApprovalEligibility({ ...base, final_review_status: "not_performed" }, good).eligible, false);
  assert.equal(getPendingApprovalEligibility(base, []).eligible, false);
  assert.equal(getPendingApprovalEligibility(base, [{ proposal_id: "p1", review: { validation_status: "unverified" } }]).eligible, false);
});

test("approval and rejection send the complete explicit proposal set; empty sets are invalid", async () => {
  const calls = [];
  const client = { post: async (...args) => { calls.push(args); return { decision: args[1].decision }; } };
  for (const decision of ["approved", "rejected"]) {
    const result = await submitApprovalDecision({ workspaceId: "123e4567-e89b-42d3-a456-426614174000", executionId: "execution-id", decision, proposalIds: ["proposal-a", "proposal-b"], client });
    assert.equal(result.decision, decision);
    assert.deepEqual(calls.at(-1)[1], { decision, proposal_ids: ["proposal-a", "proposal-b"] });
  }
  assert.throws(() => submitApprovalDecision({ workspaceId: "123e4567-e89b-42d3-a456-426614174000", executionId: "execution-id", decision: "approved", proposalIds: [], client }), /complete pending proposal set/);
});
