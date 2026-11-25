import { apiGet, apiPost, createRequestKey } from "./apiClient.js";

export const WORKSPACE_STORAGE_KEY = "codewise.workspaceId";
export const RECENT_EXECUTIONS_KEY = "codewise.recentExecutions";

export const AGENT_GROUPS = [
  { id: "code_quality_security_planning", title: "Code Quality & Security Planning", agents: [
    "code_quality_analyst", "security_analyst", "migration_planner"], description: "Quality, security, and migration analysis." },
  { id: "patch_engineering", title: "Patch Engineering", agents: ["patch_author", "test_engineer"],
    description: "Patch proposals and test specifications." },
  { id: "final_review", title: "Final Review", agents: ["review_agent"],
    description: "Independent review of the analysis and patch proposals." },
];

export const REQUIRED_AGENTS = [
  { id: "structure_analyzer", name: "Structure Analyzer" },
  { id: "business_logic_analyst", name: "Business Logic Analyst" },
];

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isValidWorkspaceId(value) {
  return UUID_PATTERN.test(String(value ?? "").trim());
}

export function getStoredWorkspaceId(storage = globalThis.localStorage) {
  try { return storage?.getItem(WORKSPACE_STORAGE_KEY) ?? ""; } catch { return ""; }
}

export function setStoredWorkspaceId(value, storage = globalThis.localStorage) {
  const normalized = String(value ?? "").trim();
  if (normalized && !isValidWorkspaceId(normalized)) throw new TypeError("Enter a valid workspace UUID.");
  try {
    if (normalized) storage?.setItem(WORKSPACE_STORAGE_KEY, normalized);
    else storage?.removeItem(WORKSPACE_STORAGE_KEY);
  } catch {
    throw new Error("Workspace selection could not be saved in this browser.");
  }
  return normalized;
}

export function validateGitHubRepositoryUrl(value) {
  const input = String(value ?? "").trim();
  if (!input) return "Enter a GitHub repository URL to continue.";
  if (/[\s\\?#]/.test(input) || input.includes("%") || [...input].some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  })) {
    return "The repository URL contains unsupported characters.";
  }
  let url;
  try { url = new URL(input); } catch { return "Enter a complete HTTPS GitHub URL."; }
  const rawAuthority = input.match(/^https:\/\/([^/?#]+)/i)?.[1]?.toLowerCase();
  const rawPath = input.replace(/^https:\/\/[^/?#]+/i, "").split(/[?#]/, 1)[0];
  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com" || url.port ||
      rawAuthority !== "github.com" || url.username || url.password || /(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) {
    return "Only HTTPS URLs hosted at github.com are supported.";
  }
  const normalizedPath = url.pathname.endsWith("/") ? url.pathname.slice(0, -1) : url.pathname;
  if (normalizedPath.includes("//") || !normalizedPath.startsWith("/")) {
    return "Enter a repository URL in the format github.com/owner/repository.";
  }
  const parts = normalizedPath.slice(1).split("/");
  if (parts.length !== 2) return "Enter a repository URL in the format github.com/owner/repository.";
  const [owner, rawName] = parts;
  const name = rawName.endsWith(".git") ? rawName.slice(0, -4) : rawName;
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(owner) ||
      !/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(name) || name === "." || name === "..") {
    return "That GitHub owner or repository name is not valid.";
  }
  return "";
}

export function selectedAgentIds(selectedGroups) {
  const groups = new Set(selectedGroups);
  return [...REQUIRED_AGENTS.map(({ id }) => id),
    ...AGENT_GROUPS.filter(({ id }) => groups.has(id)).flatMap(({ agents }) => agents)];
}

export function buildWorkflowRequest({ submissionId, selectedGroups, approvalRequired = false }) {
  if (!submissionId) throw new TypeError("A sealed repository submission is required.");
  const allowed = new Set(AGENT_GROUPS.map(({ id }) => id));
  const groups = [...new Set(selectedGroups)];
  if (groups.some((group) => !allowed.has(group))) throw new TypeError("An unknown optional agent group was selected.");
  if (approvalRequired && !(groups.includes("patch_engineering") && groups.includes("final_review"))) {
    throw new TypeError("Patch Engineering and Final Review are both required for an approval request.");
  }
  return { submission_id: submissionId, groups, approval_required: Boolean(approvalRequired) };
}

function workspacePath(workspaceId, suffix) {
  if (!isValidWorkspaceId(workspaceId)) throw new TypeError("Choose a valid workspace UUID first.");
  return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/${suffix}`;
}

export function createRepositorySubmission({ workspaceId, repositoryUrl, ref = "", refKind = "branch", idempotencyKey = createRequestKey(), client = { post: apiPost } }) {
  const validationError = validateGitHubRepositoryUrl(repositoryUrl);
  if (validationError) throw new TypeError(validationError);
  return client.post(workspacePath(workspaceId, "submissions/"), {
    repository_url: repositoryUrl.trim(), ref: ref.trim(), ref_kind: refKind,
  }, { idempotencyKey });
}

export function startWorkflow({ workspaceId, submissionId, selectedGroups, approvalRequired = false,
  idempotencyKey = createRequestKey(), client = { post: apiPost } }) {
  const body = buildWorkflowRequest({ submissionId, selectedGroups, approvalRequired });
  return client.post(workspacePath(workspaceId, "executions/"), body, { idempotencyKey });
}

export function getSubmission(workspaceId, submissionId, client = { get: apiGet }) {
  return client.get(workspacePath(workspaceId, `submissions/${encodeURIComponent(submissionId)}/`));
}

export function getExecution(workspaceId, executionId, client = { get: apiGet }) {
  return client.get(workspacePath(workspaceId, `executions/${encodeURIComponent(executionId)}/`));
}

export function getAgentResults(workspaceId, executionId, client = { get: apiGet }) {
  return client.get(workspacePath(workspaceId, `executions/${encodeURIComponent(executionId)}/agents/`));
}

export function getApprovalProposals(workspaceId, executionId, client = { get: apiGet }) {
  return client.get(workspacePath(workspaceId, `executions/${encodeURIComponent(executionId)}/approval-proposals/`));
}

export function submitApprovalDecision({ workspaceId, executionId, decision, proposalIds, client = { post: apiPost } }) {
  if (!["approved", "rejected"].includes(decision) || !Array.isArray(proposalIds) || proposalIds.length === 0) {
    throw new TypeError("A decision and the complete pending proposal set are required.");
  }
  return client.post(workspacePath(workspaceId, `executions/${encodeURIComponent(executionId)}/approval/`),
    { decision, proposal_ids: proposalIds });
}

export function readRecentExecutions(storage = globalThis.localStorage) {
  try {
    const items = JSON.parse(storage?.getItem(RECENT_EXECUTIONS_KEY) ?? "[]");
    return Array.isArray(items) ? items.filter((item) => item && typeof item.executionId === "string").slice(0, 20) : [];
  } catch { return []; }
}

export function readRecentActivity(storage = globalThis.localStorage) {
  try {
    const items = JSON.parse(storage?.getItem(RECENT_EXECUTIONS_KEY) ?? "[]");
    return Array.isArray(items) ? items.filter((item) => item && typeof item.submissionId === "string").slice(0, 20) : [];
  } catch { return []; }
}

export function rememberSubmission(item, storage = globalThis.localStorage) {
  const current = readRecentActivity(storage).filter((row) => row.submissionId !== item.submissionId);
  const next = [{ ...item, savedAt: new Date().toISOString() }, ...current].slice(0, 20);
  try { storage?.setItem(RECENT_EXECUTIONS_KEY, JSON.stringify(next)); } catch { /* local history is optional */ }
  return next;
}

export function rememberExecution(item, storage = globalThis.localStorage) {
  const activity = readRecentActivity(storage);
  const previous = activity.find((row) => row.submissionId === item.submissionId);
  const current = activity.filter((row) => row.executionId !== item.executionId && row.submissionId !== item.submissionId);
  const next = [{ ...previous, ...item, savedAt: new Date().toISOString() }, ...current].slice(0, 20);
  try { storage?.setItem(RECENT_EXECUTIONS_KEY, JSON.stringify(next)); } catch { /* local history is optional */ }
  return next;
}
