import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertCircle, ArrowLeft, ArrowRight, Check, Clock3, LoaderCircle, RefreshCw, ShieldCheck, X } from "lucide-react";
import AgentOutputSections from "@/components/AgentOutputSections.jsx";
import ExecutionStatusBadge from "@/components/ExecutionStatusBadge.jsx";
import PageHeading from "@/components/PageHeading.jsx";
import WorkspaceIdPicker from "@/components/WorkspaceIdPicker.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog.jsx";
import { getStoredWorkspaceId, getExecution, getAgentResults, getApprovalProposals, getSubmission,
  submitApprovalDecision } from "@/services/repositoryWorkflow.js";
import { buildEvidenceDescription, buildExecutionAgents, getPendingApprovalEligibility, OFFICIAL_AGENTS } from "@/services/executionViewModel.js";

export default function ExecutionPage() {
  const { executionId, agentId } = useParams();
  const [workspaceId, setWorkspaceId] = useState(getStoredWorkspaceId());
  const [execution, setExecution] = useState(null);
  const [agentPayload, setAgentPayload] = useState(null);
  const [submission, setSubmission] = useState(null);
  const [proposals, setProposals] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [decision, setDecision] = useState("");
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(async () => {
    if (!workspaceId || !executionId) return;
    setLoading(true);
    setError("");
    try {
      const response = await getExecution(workspaceId, executionId);
      const current = response.execution;
      setExecution(current);
      const [agentResponse, submissionResponse, proposalResponse] = await Promise.all([
        getAgentResults(workspaceId, executionId),
        current.submission_id ? getSubmission(workspaceId, current.submission_id).catch(() => null) : null,
        current.status === "pending_approval" ? getApprovalProposals(workspaceId, executionId) : null,
      ]);
      setAgentPayload(agentResponse);
      setSubmission(submissionResponse?.submission ?? null);
      setProposals(proposalResponse?.proposals ?? []);
    } catch (requestError) {
      setError(requestError.message || "The execution could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, executionId]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- Route entry intentionally starts an API refresh.
  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  useEffect(() => {
    if (!execution || !["queued", "running"].includes(execution.status)) return undefined;
    const timer = window.setTimeout(refresh, 1800);
    return () => window.clearTimeout(timer);
  }, [execution, refresh]);

  const agents = useMemo(() => buildExecutionAgents(execution, agentPayload), [execution, agentPayload]);
  const focusedAgent = agents.find((agent) => agent.id === agentId);
  const eligibility = getPendingApprovalEligibility(execution, proposals);
  const approvalWaiting = execution?.status === "pending_approval";

  const confirmDecision = async () => {
    if (!eligibility.eligible || !decision) return;
    setDecisionBusy(true);
    setError("");
    try {
      await submitApprovalDecision({ workspaceId, executionId, decision,
        proposalIds: proposals.map((proposal) => proposal.proposal_id) });
      setDecision("");
      await refresh();
    } catch (requestError) {
      setError(requestError.message || "The backend rejected the decision.");
    } finally {
      setDecisionBusy(false);
    }
  };

  if (!workspaceId) return <div className="mx-auto max-w-3xl space-y-6"><PageHeading eyebrow="Execution" title="Choose a workspace" description="The execution is scoped to a Django workspace membership." /><Card><CardContent className="pt-5"><WorkspaceIdPicker value={workspaceId} onChange={setWorkspaceId} /></CardContent></Card></div>;

  return <div className="mx-auto w-full max-w-6xl space-y-7 pb-12">
    <PageHeading eyebrow="Analysis execution" title={focusedAgent ? focusedAgent.name : "Execution results"}
      description={focusedAgent ? "Structured result and evidence returned by this agent." : "Execution status and structured Agent 1–8 results from the backend."}
      action={<Button asChild variant="outline"><Link to="/history"><ArrowLeft />History</Link></Button>} />

    {error && <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3.5 py-3 text-sm text-destructive"><AlertCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
    {!execution && loading && <div role="status" className="flex items-center justify-center gap-2 rounded-xl border border-border/70 py-12 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Loading execution from the API</div>}
    {!execution && !loading && error && <Button variant="outline" onClick={() => setRefreshKey((key) => key + 1)}>Try again</Button>}

    {execution && <>
      <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
        <Card className="border-border/80"><CardHeader className="flex-row items-start justify-between gap-4"><div><CardTitle className="font-mono text-base">Execution {execution.id.slice(0, 12)}</CardTitle><CardDescription className="mt-1">Commit <span className="font-mono">{execution.commit_sha}</span> · snapshot {execution.snapshot_id.slice(0, 8)}</CardDescription></div><ExecutionStatusBadge status={execution.status} /></CardHeader>
          <CardContent className="grid gap-3 text-sm sm:grid-cols-2"><div><p className="text-xs text-muted-foreground">Submission</p><p className="mt-1 font-mono text-xs">{execution.submission_id}</p><p className="mt-1 text-xs">{submission?.status ?? "Status not available"}</p></div>
            <div><p className="text-xs text-muted-foreground">Snapshot manifest</p><p className="mt-1 break-all font-mono text-[11px]">{execution.manifest_digest}</p></div>
            <div className="sm:col-span-2"><p className="text-xs text-muted-foreground">Selected agents</p><div className="mt-2 flex flex-wrap gap-1.5">{(execution.selected_agents ?? []).map((id) => <Badge key={id} variant="secondary">{OFFICIAL_AGENTS.find((agent) => agent.id === id)?.name ?? id}</Badge>)}</div></div>
            <div className="sm:col-span-2 flex flex-wrap items-center justify-between gap-2 border-t border-border/70 pt-3"><p className="text-xs text-muted-foreground">Workflow status comes from persisted API state. Agent progress is shown only when the runtime reports it.</p><Button variant="ghost" size="sm" onClick={() => setRefreshKey((key) => key + 1)} disabled={loading}><RefreshCw className={`mr-1 size-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</Button></div>
          </CardContent>
        </Card>
        <Card className="border-border/80 lg:w-80"><CardHeader><CardTitle className="flex items-center gap-2 text-sm"><Clock3 className="size-4 text-primary" />Lifecycle</CardTitle></CardHeader><CardContent className="space-y-2 text-xs text-muted-foreground"><p>Created: {execution.created_at ? new Date(execution.created_at).toLocaleString() : "Not provided"}</p><p>Started: {execution.started_at ? new Date(execution.started_at).toLocaleString() : "Not started"}</p><p>Finished: {execution.finished_at ? new Date(execution.finished_at).toLocaleString() : "Still running or not started"}</p>{execution.error_code && <p className="rounded bg-destructive/5 p-2 text-destructive">Error: {execution.error_code.replaceAll("_", " ")}</p>}</CardContent></Card>
      </div>

      {approvalWaiting && <Card className="border-amber-500/30 bg-amber-500/[0.035]">
        <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-5 text-amber-500" />Human review requested</CardTitle><CardDescription>AI readiness is a recommendation only. A workspace owner or admin must decide; the backend validates the decision and does not apply or publish patches.</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border border-border/70 bg-background/45 p-3 text-sm"><p><strong>Agent 8 outcome:</strong> {String(execution.final_review_status ?? "unknown").replaceAll("_", " ")}</p><p className="mt-1 text-xs text-muted-foreground">The API returned {proposals.length} pending proposal(s). {eligibility.reason}</p></div>
          {proposals.map((proposal) => <div key={proposal.proposal_id} className="rounded-xl border border-border/70 bg-background/45 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-mono text-sm font-semibold">{proposal.proposal_id}</h3><Badge variant="outline">{proposal.risk ?? "risk not provided"}</Badge></div>
            <p className="mt-2 text-xs text-muted-foreground">{proposal.path} · {proposal.review?.validation_status ?? "review validation not returned"}</p>
            <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{proposal.expected_behavior ?? proposal.rationale ?? "No summary was provided."}</p>
            {proposal.rationale && <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{proposal.rationale}</p>}
            {proposal.required_tests?.length > 0 && <div className="mt-3"><p className="text-[10px] font-semibold uppercase text-muted-foreground">Required tests (proposed, not executed)</p><ul className="mt-1 list-inside list-disc text-xs">{proposal.required_tests.map((item) => <li key={item}>{item}</li>)}</ul></div>}
            {proposal.review?.detail && <p className="mt-3 rounded-md bg-muted/40 p-2 text-xs leading-5">Agent 8: {proposal.review.detail}</p>}
            {Array.isArray(proposal.evidence) && proposal.evidence.length > 0 && <div className="mt-3"><p className="text-[10px] font-semibold uppercase text-muted-foreground">Proposal evidence</p><ul className="mt-1 space-y-1">{proposal.evidence.map((item, index) => <li key={item.evidence_id ?? index} className="break-all rounded bg-muted/35 px-2 py-1 font-mono text-[11px]">{buildEvidenceDescription(item)}</li>)}</ul></div>}
            {[...(Array.isArray(proposal.warnings) ? proposal.warnings : []), ...(Array.isArray(proposal.review?.warnings) ? proposal.review.warnings : [])].length > 0 && <ul className="mt-3 space-y-1 text-xs text-amber-500">{[...(Array.isArray(proposal.warnings) ? proposal.warnings : []), ...(Array.isArray(proposal.review?.warnings) ? proposal.review.warnings : [])].map((warning, index) => <li key={index}>Warning: {typeof warning === "string" ? warning : JSON.stringify(warning)}</li>)}</ul>}
            {(proposal.unified_diff || proposal.diff) && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-primary">Inspect proposed diff</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-muted/50 p-3 text-xs">{proposal.unified_diff ?? proposal.diff}</pre></details>}
          </div>)}
          <div className="flex flex-wrap gap-2"><Button onClick={() => setDecision("approved")} disabled={!eligibility.eligible || decisionBusy}><Check />Approve proposals</Button><Button variant="destructive" onClick={() => setDecision("rejected")} disabled={!eligibility.eligible || decisionBusy}><X />Reject proposals</Button></div>
          {!eligibility.eligible && <p className="text-xs text-muted-foreground">Decision controls are disabled because this response is not eligible. Server-side permission and integrity checks always apply.</p>}
        </CardContent>
      </Card>}

      <section className="space-y-4" aria-labelledby="agent-results-heading">
        <div className="flex flex-wrap items-end justify-between gap-3"><div><h2 id="agent-results-heading" className="text-lg font-semibold">Agent results</h2><p className="mt-1 text-sm text-muted-foreground">Lifecycle state, failures, evidence, and uncertainty are preserved from each structured result.</p></div><Badge variant="outline">{agents.filter((agent) => agent.selected).length} selected</Badge></div>
        {focusedAgent && <div className="flex items-center justify-between rounded-lg border border-border/70 bg-muted/25 px-3 py-2"><p className="text-xs text-muted-foreground">Viewing {focusedAgent.name}</p><Button asChild size="sm" variant="ghost"><Link to={`/executions/${executionId}`}>All agents</Link></Button></div>}
        <div className="space-y-3">{(focusedAgent ? [focusedAgent] : agents).map((agent) => <details key={agent.id} open={Boolean(focusedAgent) || undefined} className="group rounded-xl border border-border/70 bg-card/70">
          <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 p-4 [&::-webkit-details-marker]:hidden"><div className="flex min-w-0 items-center gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-semibold text-muted-foreground">{OFFICIAL_AGENTS.findIndex((item) => item.id === agent.id) + 1}</span><span className="min-w-0"><span className="block text-sm font-semibold">{agent.name}</span><span className="mt-0.5 block text-xs text-muted-foreground">{agent.selected ? "Selected" : "Not selected"}{agent.errorCodes.length ? ` · ${agent.errorCodes.join(", ")}` : ""}</span></span></div><div className="flex items-center gap-2"><ExecutionStatusBadge status={agent.status} /><span className="text-muted-foreground transition-transform group-open:rotate-90"><ArrowRight className="size-4" /></span></div></summary>
          <div className="border-t border-border/70 p-4"><AgentOutputSections agentId={agent.id} output={agent.output} />
            {agent.output && <div className="mt-3 flex justify-end"><Button asChild size="sm" variant="ghost"><Link to={`/executions/${executionId}/agents/${agent.id}`}>Open {agent.name} result</Link></Button></div>}
          </div>
        </details>)}</div>
      </section>
    </>}

    <Dialog open={Boolean(decision)} onOpenChange={(open) => !open && !decisionBusy && setDecision("")}>
      <DialogContent><DialogHeader><DialogTitle>Confirm {decision === "approved" ? "approval" : "rejection"}</DialogTitle>
        <DialogDescription>{decision === "approved" ? "You are recording a human approval for the complete listed proposal set." : "You are rejecting the complete listed proposal set."} This decision will be attributed to your authenticated account and cannot be used to apply or publish a patch.</DialogDescription></DialogHeader>
        <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs"><p>Execution: <span className="font-mono">{executionId}</span></p><p className="mt-1">Proposals: {proposals.map((item) => item.proposal_id).join(", ")}</p></div>
        <DialogFooter><Button variant="outline" onClick={() => setDecision("")} disabled={decisionBusy}>Cancel</Button><Button variant={decision === "rejected" ? "destructive" : "default"} onClick={confirmDecision} disabled={decisionBusy || !eligibility.eligible}>{decisionBusy ? <><LoaderCircle className="animate-spin" />Sending decision</> : `Confirm ${decision}`}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}
