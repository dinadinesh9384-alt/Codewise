import { AGENT_GROUPS, REQUIRED_AGENTS } from "./repositoryWorkflow.js";

export const OFFICIAL_AGENTS = [
  ...REQUIRED_AGENTS,
  ...AGENT_GROUPS.flatMap((group) => group.agents.map((id) => ({ id, name: id.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase()) }))),
];

const STATUS_DISPLAY = {
  queued: { label: "Queued", tone: "muted" },
  running: { label: "Running", tone: "active" },
  pending_approval: { label: "Pending human decision", tone: "warning" },
  completed: { label: "Completed", tone: "success" },
  partial: { label: "Partial results", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "muted" },
  timed_out: { label: "Timed out", tone: "danger" },
  skipped: { label: "Skipped", tone: "muted" },
  not_performed: { label: "Not performed", tone: "muted" },
  unavailable: { label: "Unavailable", tone: "warning" },
};

export function statusDisplay(status) {
  return STATUS_DISPLAY[status] ?? { label: status ? String(status).replaceAll("_", " ") : "Unknown", tone: "muted" };
}

export function buildExecutionAgents(execution, resultPayload) {
  const statuses = new Map((execution?.agent_statuses ?? []).map((item) => [item.agent_id, item]));
  const outputs = new Map((resultPayload?.agents ?? []).map((item) => [item.agent_id, item]));
  const selected = new Set(execution?.selected_agents ?? []);
  return OFFICIAL_AGENTS.map((agent) => {
    const lifecycle = statuses.get(agent.id) ?? outputs.get(agent.id);
    const result = outputs.get(agent.id) ?? lifecycle;
    return {
      ...agent,
      selected: selected.has(agent.id),
      status: lifecycle?.status ?? "unavailable",
      errorCodes: lifecycle?.error_codes ?? (lifecycle?.error_code ? [lifecycle.error_code] : []),
      output: result?.output ?? null,
    };
  });
}

const SECTIONS = [
  ["findings", "Findings"], ["workflows", "Workflows"], ["business_rules", "Business rules"],
  ["entities", "Entities"], ["relationships", "Relationships"], ["complexity_metrics", "Complexity metrics"],
  ["function_metrics", "Function metrics"], ["duplication_groups", "Duplication groups"],
  ["assessments", "Assessments"], ["insights", "Security insights"], ["recommendations", "Recommendations"],
  ["tasks", "Migration tasks"], ["risks", "Risks"], ["strategies", "Strategies"], ["phases", "Plan phases"],
  ["proposals", "Patch proposals"], ["patch_analyses", "Patch analysis"], ["scenarios", "Test specifications"],
  ["test_code_proposals", "Test code proposals"], ["test_files", "Test inventory"],
  ["coverage_observations", "Coverage observations"], ["reviewed_agents", "Reviewed agent provenance"],
  ["patch_reviews", "Patch reviews"], ["evidence_validation", "Evidence validation"],
  ["conflicts", "Review conflicts"], ["evidence", "Evidence"], ["uncertainties", "Uncertainty"],
  ["limitations", "Limitations"], ["validation_errors", "Validation warnings"],
  ["unavailable_agents", "Unavailable agents"], ["skipped_agents", "Skipped agents"],
];

export function buildOutputSections(output) {
  if (!output || typeof output !== "object") return [];
  return SECTIONS.filter(([key]) => Array.isArray(output[key]) && output[key].length > 0)
    .map(([key, title]) => ({ key, title, items: output[key] }));
}

export function buildEvidenceDescription(item) {
  if (!item || typeof item !== "object") return String(item ?? "");
  const path = item.path ?? item.affected_path ?? item.report_path;
  const line = item.start_line ? `:${item.start_line}` : "";
  const hash = item.content_hash ?? item.sha256;
  const parts = [path ? `${path}${line}` : null, item.evidence_id ?? item.item_id ?? item.finding_id,
    hash ? `SHA-256 ${hash}` : null, item.validation_status, item.uncertainty].filter(Boolean);
  return parts.length ? parts.join(" · ") : JSON.stringify(item, null, 2);
}

export function getPendingApprovalEligibility(execution, proposals) {
  if (execution?.status !== "pending_approval" || execution?.approval !== "pending") {
    return { eligible: false, reason: "There is no pending human approval interrupt." };
  }
  if (!["ready_for_human_review", "ready_with_warnings"].includes(execution.final_review_status)) {
    return { eligible: false, reason: "Agent 8 did not report a ready review. The backend remains authoritative." };
  }
  if (!Array.isArray(proposals) || proposals.length === 0) {
    return { eligible: false, reason: "No pending patch proposals were returned." };
  }
  const unverified = proposals.filter((item) => item.review?.validation_status !== "verified_in_memory" || item.review?.applied);
  if (unverified.length) return { eligible: false, reason: "At least one proposal lacks verified Agent 8 review provenance." };
  return { eligible: true, reason: "The API returned a pending proposal set and ready Agent 8 review. The backend will revalidate the decision." };
}
