import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, ArrowRight, Check, CircleHelp, Code2, LockKeyhole, LoaderCircle, ShieldCheck, Users } from "lucide-react";
import PageHeading from "@/components/PageHeading.jsx";
import WorkspaceIdPicker from "@/components/WorkspaceIdPicker.jsx";
import { Badge } from "@/components/ui/badge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { Input } from "@/components/ui/input.jsx";
import { AGENT_GROUPS, REQUIRED_AGENTS, createRepositorySubmission, getStoredWorkspaceId,
  isValidWorkspaceId, rememberExecution, rememberSubmission, selectedAgentIds, startWorkflow,
  validateGitHubRepositoryUrl } from "@/services/repositoryWorkflow.js";
import { createRequestKey } from "@/services/apiClient.js";

function GitHubMark({ className = "" }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor" className={className}><path d="M12 .9a11.1 11.1 0 0 0-3.51 21.63c.55.1.76-.24.76-.54v-2.1c-3.1.68-3.76-1.32-3.76-1.32-.5-1.3-1.24-1.65-1.24-1.65-1.02-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1 .1.72 2.47 3.37 1.9.1-.73.4-1.23.7-1.52-2.48-.29-5.09-1.24-5.09-5.52 0-1.22.44-2.22 1.16-3-.12-.29-.5-1.44.11-3 0 0 .95-.3 3.05 1.15a10.6 10.6 0 0 1 5.55 0c2.1-1.45 3.05-1.15 3.05-1.15.61 1.56.23 2.71.11 3 .72.78 1.16 1.78 1.16 3 0 4.29-2.61 5.23-5.1 5.51.4.35.76 1.03.76 2.08v3.1c0 .3.2.65.77.54A11.1 11.1 0 0 0 12 .9Z" /></svg>;
}

export default function RepositorySubmissionPage() {
  const navigate = useNavigate();
  const [workspaceId, setWorkspaceId] = useState(getStoredWorkspaceId());
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [refKind, setRefKind] = useState("branch");
  const [touched, setTouched] = useState(false);
  const [groups, setGroups] = useState(() => Object.fromEntries(AGENT_GROUPS.map(({ id }) => [id, true])));
  const [approvalRequired, setApprovalRequired] = useState(false);
  const [phase, setPhase] = useState("idle");
  const [submission, setSubmission] = useState(null);
  const [error, setError] = useState("");
  const [validationError, setValidationError] = useState("");
  const submissionKey = useRef(createRequestKey());
  const executionKey = useRef(createRequestKey());
  const selectedGroups = useMemo(() => AGENT_GROUPS.filter(({ id }) => groups[id]).map(({ id }) => id), [groups]);
  const agentCount = selectedAgentIds(selectedGroups).length;
  const urlError = (touched || url) ? validateGitHubRepositoryUrl(url) : "";

  const updateUrl = (value) => {
    setUrl(value);
    setSubmission(null);
    setError("");
    submissionKey.current = createRequestKey();
    executionKey.current = createRequestKey();
  };

  const runAnalysis = async (activeSubmission) => {
    if (!isValidWorkspaceId(workspaceId)) {
      setError("Save a valid workspace UUID before starting analysis.");
      return;
    }
    if (!activeSubmission?.id) {
      setError("A sealed repository submission is required before analysis can start.");
      return;
    }
    setError("");
    setPhase("starting");
    try {
      const response = await startWorkflow({ workspaceId, submissionId: activeSubmission.id,
        selectedGroups, approvalRequired, idempotencyKey: executionKey.current });
      const execution = response?.execution;
      if (!execution?.id) throw new Error("The API response did not include an execution ID.");
      rememberExecution({ executionId: execution.id, submissionId: activeSubmission.id, workspaceId,
        repositoryUrl: activeSubmission.submitted_url ?? url, selectedAgents: execution.selected_agents,
        selectedGroups: execution.selected_groups, status: execution.status });
      navigate(`/executions/${execution.id}`);
    } catch (requestError) {
      setPhase("submitted");
      setError(requestError.message || "The analysis could not be started.");
      if (["workflow_runtime_unavailable", "durable_approval_checkpoint_unavailable", "approval_agents_not_selected"].includes(requestError.code)) {
        executionKey.current = createRequestKey();
      }
    }
  };

  const submit = async (event) => {
    event.preventDefault();
    setTouched(true);
    const invalidUrl = validateGitHubRepositoryUrl(url);
    if (invalidUrl) { setValidationError(invalidUrl); return; }
    setValidationError("");
    if (!isValidWorkspaceId(workspaceId)) {
      setError("Save a valid workspace UUID before submitting a repository.");
      return;
    }
    setError("");
    setPhase("submitting");
    try {
      const response = await createRepositorySubmission({ workspaceId, repositoryUrl: url,
        ref, refKind, idempotencyKey: submissionKey.current });
      const created = response?.submission;
      if (!created?.id || created.status !== "sealed" || !created.snapshot_id) {
        setSubmission(created ?? null);
        setPhase("submitted");
        throw new Error(created?.error_code
          ? `Repository submission failed (${created.error_code.replaceAll("_", " ")}).`
          : `Snapshot submission returned status “${created?.status ?? "unknown"}”; it is not ready for analysis.`);
      }
      const submissionRecord = { ...created, submitted_url: url.trim(), ref: ref.trim() };
      setSubmission(submissionRecord);
      rememberSubmission({ submissionId: created.id, workspaceId, repositoryUrl: url.trim(),
        status: created.status, snapshotId: created.snapshot_id });
      await runAnalysis(submissionRecord);
    } catch (requestError) {
      setPhase((current) => current === "submitting" ? "idle" : current);
      setError(requestError.message || "The repository could not be submitted.");
    }
  };

  const busy = phase === "submitting" || phase === "starting";
  const updateGroup = (id) => {
    setGroups((current) => ({ ...current, [id]: !current[id] }));
    setError("");
    executionKey.current = createRequestKey();
  };

  return <div className="mx-auto w-full max-w-6xl space-y-7 pb-10 sm:space-y-9">
    <PageHeading eyebrow="Repository workspace" title="Analyze a repository" description="Seal a public GitHub snapshot, choose the specialist teams, and start a synchronous analysis." />

    <Card className="border-border/80 bg-card/90">
      <CardHeader><CardTitle>Workspace access</CardTitle><CardDescription>CodeWise uses your existing Django session. The workspace UUID selects where the authenticated request is scoped.</CardDescription></CardHeader>
      <CardContent><WorkspaceIdPicker value={workspaceId} onChange={setWorkspaceId} /></CardContent>
    </Card>

    <form onSubmit={submit} noValidate className="space-y-6">
      <Card className="border-border/80 bg-card/90 shadow-lg shadow-black/5">
        <CardHeader className="gap-2 border-b border-border/60 px-5 pb-5 sm:px-6">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-primary"><GitHubMark className="size-4" />Repository source</div>
          <CardTitle className="text-lg">Submit a public GitHub repository</CardTitle>
          <CardDescription>The backend resolves a Git reference, verifies its archive, and seals an immutable snapshot before analysis starts.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 px-5 pt-5 sm:px-6">
          <div className="space-y-2">
            <label htmlFor="repository-url" className="text-sm font-medium">GitHub repository URL</label>
            <div className="relative"><GitHubMark className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input id="repository-url" type="url" autoComplete="url" placeholder="https://github.com/owner/repository" value={url}
                onChange={(event) => { updateUrl(event.target.value); setValidationError(""); }} onBlur={() => setTouched(true)}
                aria-invalid={Boolean(urlError || validationError)} aria-describedby={urlError || validationError ? "repository-url-error" : "repository-url-hint"}
                className="h-12 pl-10 text-sm sm:text-base" />
            </div>
            {(urlError || validationError) ? <p id="repository-url-error" className="flex items-center gap-1.5 text-xs text-destructive" role="alert"><AlertCircle className="size-3.5 shrink-0" />{validationError || urlError}</p>
              : <p id="repository-url-hint" className="text-xs text-muted-foreground">Only HTTPS public repositories on github.com are supported. Private repository access is not implemented.</p>}
          </div>
          <div className="grid gap-4 sm:grid-cols-[1fr_12rem_auto] sm:items-end">
            <div className="space-y-2"><label htmlFor="repository-ref" className="text-sm font-medium">Branch, tag, or commit <span className="text-muted-foreground">(optional)</span></label>
              <Input id="repository-ref" value={ref} maxLength={255} onChange={(event) => { setRef(event.target.value); submissionKey.current = createRequestKey(); setSubmission(null); }} placeholder="Default branch" />
            </div>
            <div className="space-y-2"><label htmlFor="repository-ref-kind" className="text-sm font-medium">Reference type</label>
              <select id="repository-ref-kind" value={refKind} onChange={(event) => { setRefKind(event.target.value); submissionKey.current = createRequestKey(); setSubmission(null); }} className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Git reference type">
                <option value="branch">Branch</option><option value="tag">Tag</option><option value="commit">Commit SHA</option>
              </select>
            </div>
            <Badge variant="outline" className="h-10 justify-center gap-2 px-3"><ShieldCheck className="size-4 text-primary" />Snapshot integrity checked</Badge>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/80 bg-card/90">
        <CardHeader className="flex-row items-center justify-between gap-3 px-5 pb-4 sm:px-6">
          <div><CardTitle className="text-lg">Agent teams</CardTitle><CardDescription className="mt-1">Agents 1 and 2 are mandatory. Optional teams are selected by default.</CardDescription></div>
          <Badge variant="outline" className="shrink-0 gap-1.5 px-2.5 py-1"><Users className="size-3.5" />{agentCount} agents</Badge>
        </CardHeader>
        <CardContent className="space-y-5 px-5 sm:px-6">
          <section aria-labelledby="required-agents-title" className="space-y-2.5">
            <div className="flex items-center justify-between"><h3 id="required-agents-title" className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">Always included</h3><span className="text-xs text-muted-foreground">Required</span></div>
            {REQUIRED_AGENTS.map((agent, index) => <div key={agent.id} className="flex items-center gap-3 rounded-xl border border-border/70 bg-background/45 p-3.5">
              <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-sm font-semibold text-primary">0{index + 1}</span>
              <span className="min-w-0 flex-1 text-sm font-medium">{agent.name}</span><Badge variant="secondary" className="gap-1.5"><LockKeyhole className="size-3" />Required</Badge>
            </div>)}
          </section>
          <fieldset className="space-y-2.5"><legend className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">Optional groups</legend>
            {AGENT_GROUPS.map((group) => <label key={group.id} className={`flex cursor-pointer items-start gap-3.5 rounded-xl border p-3.5 transition-colors focus-within:ring-2 focus-within:ring-ring/50 sm:items-center sm:p-4 ${groups[group.id] ? "border-primary/40 bg-primary/[0.045]" : "border-border/70 bg-background/35 hover:bg-muted/30"}`}>
              <input type="checkbox" checked={groups[group.id]} onChange={() => updateGroup(group.id)} disabled={busy}
                aria-label={`Include ${group.title}`} className="mt-1 size-4 accent-primary sm:mt-0" />
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">{group.id === "patch_engineering" ? <Code2 className="size-4" /> : group.id === "final_review" ? <Check className="size-4" /> : <ShieldCheck className="size-4" />}</span>
              <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{group.title}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{group.description} Includes {group.agents.join(", ")}.</span></span>
              <Badge variant="outline" className="hidden shrink-0 sm:inline-flex">{group.agents.length} {group.agents.length === 1 ? "agent" : "agents"}</Badge>
            </label>)}
          </fieldset>

          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border/70 bg-background/35 p-3.5">
            <input type="checkbox" checked={approvalRequired} onChange={(event) => { setApprovalRequired(event.target.checked); executionKey.current = createRequestKey(); }} disabled={busy || !groups.patch_engineering || !groups.final_review} className="mt-1 size-4 accent-primary" />
            <span><span className="block text-sm font-medium">Pause for a human decision on patch proposals</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">Requires Patch Engineering, Final Review, and a durable approval checkpoint on the backend. The API may reject this request when durable resume is unavailable.</span></span>
          </label>

          {error && <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3.5 py-3 text-sm leading-5 text-destructive"><AlertCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
          {phase === "submitting" && <p className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3.5 py-3 text-sm" role="status" aria-live="polite"><LoaderCircle className="size-4 animate-spin text-primary" />Submitting repository and verifying the snapshot. This request is synchronous.</p>}
          {phase === "starting" && <p className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3.5 py-3 text-sm" role="status" aria-live="polite"><LoaderCircle className="size-4 animate-spin text-primary" />The backend is running the selected agents in this request. No background progress is simulated.</p>}
          {submission && phase !== "submitting" && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3.5 py-3 text-sm">
            <span><strong>Snapshot sealed.</strong> Submission {submission.id.slice(0, 8)} Â· {submission.status}</span>
            {phase === "submitted" && <Button type="button" size="sm" onClick={() => runAnalysis(submission)} disabled={busy}>Retry analysis request <ArrowRight /></Button>}
          </div>}
          <div className="flex flex-col-reverse gap-3 border-t border-border/60 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><CircleHelp className="mt-0.5 size-3.5 shrink-0 text-primary" />The backend remains authoritative for workspace access and approvals.</p>
            <Button type="submit" className="min-h-11 w-full gap-2 sm:w-auto" disabled={busy || !isValidWorkspaceId(workspaceId)}>
              {busy ? <><LoaderCircle className="animate-spin" />Working...</> : <>Submit and analyze <ArrowRight className="size-4" /></>}
            </Button>
          </div>
          <p className="text-right text-[11px] text-muted-foreground">All groups selected by default Â· {agentCount} agents selected</p>
        </CardContent>
      </Card>
    </form>
  </div>;
}
