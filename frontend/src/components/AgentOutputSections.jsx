import { buildEvidenceDescription, buildOutputSections } from "@/services/executionViewModel.js";
import { Badge } from "@/components/ui/badge.jsx";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card.jsx";

const HIDDEN_KEYS = new Set(["evidence", "unified_diff", "diff", "code"]);
const LABELS = {
  evidence_ids: "Evidence IDs", finding_ids: "Finding IDs", related_files: "Related files",
  affected_files: "Affected files", impacted_paths: "Impacted paths", uncertainty: "Uncertainty",
  validation_status: "Validation", execution_status: "Execution", provenance: "Provenance",
  independently_verified: "Independently verified", confidence: "Confidence", priority: "Priority",
  risk: "Risk", status: "Status", assessment: "Assessment", rationale: "Rationale",
  expected_outcome: "Expected outcome", expected_behavior: "Expected behavior", required_tests: "Required tests",
  original_content_hash: "Original content SHA-256", proposed_content_hash: "Proposed content SHA-256",
};

function titleFor(item, index) {
  if (typeof item !== "object" || item === null) return `Item ${index + 1}`;
  return item.title ?? item.name ?? item.action ?? item.task_id ?? item.finding_id ?? item.proposal_id ??
    item.scenario_id ?? item.evidence_id ?? `Item ${index + 1}`;
}

function InlineValue({ value }) {
  if (Array.isArray(value)) return <span>{value.map((item) => typeof item === "object" ? buildEvidenceDescription(item) : String(item)).join(", ")}</span>;
  if (value && typeof value === "object") return <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 text-xs leading-5">{JSON.stringify(value, null, 2)}</pre>;
  return <span className="break-words">{String(value)}</span>;
}

function StructuredItem({ item, index, sectionKey }) {
  if (typeof item !== "object" || item === null) return <li key={index} className="rounded-lg border border-border/70 bg-background/40 px-3 py-2 text-sm"><InlineValue value={item} /></li>;
  const summaryKeys = ["description", "explanation", "rationale", "assessment", "statement", "purpose", "action", "details", "executive_summary"];
  const summary = summaryKeys.map((key) => item[key]).find((value) => typeof value === "string" && value.trim());
  const tags = [item.severity, item.risk, item.status, item.validation_status, item.priority ? `Priority ${item.priority}` : null].filter(Boolean);
  const details = Object.entries(item).filter(([key, value]) => !HIDDEN_KEYS.has(key) && !summaryKeys.includes(key) &&
    value !== null && value !== undefined && value !== "" && !(Array.isArray(value) && value.length === 0));
  return <li key={item.id ?? item.proposal_id ?? item.finding_id ?? index} className="rounded-xl border border-border/70 bg-background/40 p-4">
    <div className="flex flex-wrap items-start justify-between gap-2"><h4 className="text-sm font-semibold">{titleFor(item, index)}</h4>
      {tags.length > 0 && <div className="flex flex-wrap gap-1.5">{tags.map((tag, tagIndex) => <Badge key={`${tag}-${tagIndex}`} variant="outline" className="text-[10px]">{String(tag).replaceAll("_", " ")}</Badge>)}</div>}
    </div>
    {summary && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{summary}</p>}
    {item.path && <p className="mt-2 font-mono text-xs text-primary">{item.path}{item.start_line ? `:${item.start_line}` : ""}</p>}
    {details.length > 0 && <dl className="mt-3 grid gap-x-5 gap-y-2 sm:grid-cols-2">{details.map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{LABELS[key] ?? key.replaceAll("_", " ")}</dt><dd className="mt-0.5 text-xs leading-5"><InlineValue value={value} /></dd></div>)}</dl>}
    {Array.isArray(item.evidence) && item.evidence.length > 0 && <div className="mt-3 border-t border-border/70 pt-3"><p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Evidence and provenance</p><ul className="space-y-1.5">{item.evidence.map((evidence, evidenceIndex) => <li key={evidence.evidence_id ?? evidenceIndex} className="rounded-md bg-muted/35 px-2.5 py-2 font-mono text-[11px] leading-5">{buildEvidenceDescription(evidence)}</li>)}</ul></div>}
    {(item.unified_diff || item.diff) && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-primary">View proposed diff</summary><pre className="mt-2 max-h-96 overflow-auto rounded-lg border border-border/70 bg-muted/45 p-3 text-xs leading-5">{item.unified_diff ?? item.diff}</pre></details>}
    {item.code && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-primary">View proposed test code</summary><pre className="mt-2 max-h-96 overflow-auto rounded-lg border border-border/70 bg-muted/45 p-3 text-xs leading-5">{item.code}</pre></details>}
    {sectionKey === "scenarios" && item.execution_status === "not_executed" && <p className="mt-3 text-xs text-amber-500">This is a proposed test specification. It was not executed.</p>}
  </li>;
}

export default function AgentOutputSections({ agentId, output }) {
  const sections = buildOutputSections(output);
  const summary = output?.executive_summary ?? output?.overview ?? output?.summary ?? output?.executive_overview;
  if (!output) return <div className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">No structured result was returned for this agent.</div>;
  return <div className="space-y-4">
    {summary && <Card className="border-primary/20 bg-primary/[0.035]"><CardContent className="pt-4"><p className="whitespace-pre-wrap text-sm leading-6">{summary}</p></CardContent></Card>}
    {agentId === "review_agent" && output.review_status && <div className="rounded-lg border border-border/70 px-3 py-2 text-sm"><span className="text-muted-foreground">Agent 8 outcome: </span><strong>{String(output.review_status).replaceAll("_", " ")}</strong><span className="ml-2 text-xs text-muted-foreground">AI review is not human approval.</span></div>}
    {output.execution_status === "not_executed" && <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-500">Test specifications are proposals only; the backend did not execute repository tests.</p>}
    {sections.map((section) => <Card key={section.key} className="border-border/70"><CardHeader className="pb-3"><CardTitle className="text-sm">{section.title}<span className="ml-2 text-xs font-normal text-muted-foreground">{section.items.length}</span></CardTitle></CardHeader><CardContent><ul className="space-y-2.5">{section.items.map((item, index) => <StructuredItem key={item?.id ?? item?.proposal_id ?? item?.finding_id ?? item?.task_id ?? index} item={item} index={index} sectionKey={section.key} />)}</ul></CardContent></Card>)}
    {!summary && sections.length === 0 && <p className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">The validated output contains no findings or detailed sections.</p>}
  </div>;
}
