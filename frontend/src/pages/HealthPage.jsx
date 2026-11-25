import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Clock3, FolderGit2, GitBranch, LoaderCircle, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import BackendHealthCard from "@/features/backend-health/BackendHealthCard.jsx";
import useBackendHealth from "@/hooks/useBackendHealth.js";
import PageHeading from "@/components/PageHeading.jsx";
import WorkspaceIdPicker from "@/components/WorkspaceIdPicker.jsx";
import ExecutionStatusBadge from "@/components/ExecutionStatusBadge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { getExecution, getStoredWorkspaceId, getSubmission, readRecentActivity } from "@/services/repositoryWorkflow.js";

export default function HealthPage() {
  const health = useBackendHealth();
  const [workspaceId, setWorkspaceId] = useState(getStoredWorkspaceId());
  const [activity, setActivity] = useState(readRecentActivity());
  const [records, setRecords] = useState([]);
  const [loadState, setLoadState] = useState("idle");
  const [loadError, setLoadError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(async () => {
    const saved = readRecentActivity().filter((item) => !workspaceId || item.workspaceId === workspaceId);
    setActivity(saved);
    setLoadError("");
    if (!workspaceId || !saved.length) {
      setRecords([]);
      setLoadState("ready");
      return;
    }
    setLoadState("loading");
    const results = await Promise.all(saved.map(async (item) => {
      let submission;
      try { submission = (await getSubmission(workspaceId, item.submissionId)).submission; }
      catch (error) { return { ...item, execution: null, error: error.message }; }
      if (!item.executionId) return { ...item, submission, execution: null };
      try {
        const response = await getExecution(workspaceId, item.executionId);
        return { ...item, submission, execution: response.execution };
      } catch (error) {
        return { ...item, submission, execution: null, error: error.message };
      }
    }));
    setRecords(results);
    const inaccessible = results.find((item) => item.error);
    if (inaccessible && results.every((item) => item.error)) setLoadError(inaccessible.error);
    setLoadState("ready");
  }, [workspaceId]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- Route entry intentionally starts an API refresh.
  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  const counts = useMemo(() => ({
    repositories: new Set(activity.map((item) => item.repositoryUrl).filter(Boolean)).size,
    runs: records.filter((item) => item.execution).length,
    partial: records.filter((item) => item.execution?.status === "partial").length,
  }), [activity, records]);

  return <div className="mx-auto w-full max-w-6xl space-y-8 pb-10">
    <PageHeading eyebrow="Engineering workspace" title="Repository intelligence, in one place" description="Submit a public repository, choose analysis teams, and inspect results returned by the CodeWise backend." action={<Button asChild><Link to="/repositories"><Plus />Analyze repository</Link></Button>} />

    <Card className="border-border/80 bg-card/90"><CardHeader><CardTitle>Workspace</CardTitle><CardDescription>Use the UUID of a workspace where your Django session has membership.</CardDescription></CardHeader><CardContent><WorkspaceIdPicker value={workspaceId} onChange={(value) => { setWorkspaceId(value); setRefreshKey((key) => key + 1); }} /></CardContent></Card>

    <div className="grid gap-3 sm:grid-cols-3">
      {[
        { label: "Repositories in local history", value: workspaceId ? String(counts.repositories) : "—", icon: GitBranch },
        { label: "Executions found", value: workspaceId ? String(counts.runs) : "—", icon: Clock3 },
        { label: "Partial results", value: workspaceId ? String(counts.partial) : "—", icon: ShieldCheck },
      ].map(({ label, value, icon: Icon }) => <div key={label} className="flex items-center gap-3 rounded-xl border border-border/70 bg-card/70 px-4 py-3.5"><span className="flex size-9 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Icon className="size-4" /></span><div><p className="text-xs text-muted-foreground">{label}</p><p className="mt-0.5 text-sm font-semibold">{value}</p></div></div>)}
    </div>

    <div className="grid gap-5 lg:grid-cols-[1.3fr_0.7fr]">
      <Card className="border-border/70">
        <CardHeader className="flex-row items-start justify-between gap-4"><div><CardTitle>Recent analysis</CardTitle><CardDescription className="mt-1">The API does not provide a workspace history list; this view uses runs saved by this browser.</CardDescription></div><Button variant="ghost" size="icon" aria-label="Refresh recent executions" onClick={() => setRefreshKey((key) => key + 1)} disabled={loadState === "loading"}><RefreshCw className={`size-4 ${loadState === "loading" ? "animate-spin" : ""}`} /></Button></CardHeader>
        <CardContent>
          {!workspaceId && <div className="rounded-xl border border-dashed border-border px-5 py-8 text-center"><FolderGit2 className="mx-auto mb-3 size-6 text-muted-foreground" /><p className="text-sm font-medium">Choose a workspace to view activity</p><p className="mt-1 text-xs text-muted-foreground">Workspace discovery is not available in the current API.</p></div>}
          {workspaceId && loadState === "loading" && <p className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground" role="status"><LoaderCircle className="size-4 animate-spin" />Loading saved executions</p>}
          {workspaceId && loadError && <div className="rounded-lg border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive" role="alert">{loadError}</div>}
          {workspaceId && loadState === "ready" && !records.length && <div className="rounded-xl border border-dashed border-border px-5 py-8 text-center"><FolderGit2 className="mx-auto mb-3 size-6 text-muted-foreground" /><p className="text-sm font-medium">No saved analyses yet</p><p className="mt-1 text-xs text-muted-foreground">Start with a public repository. Runs are remembered in this browser.</p><Button asChild variant="outline" size="sm" className="mt-4"><Link to="/repositories">Submit repository <ArrowRight /></Link></Button></div>}
          {records.length > 0 && <div className="divide-y divide-border/70">{records.map((item) => <div key={item.submissionId} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0"><p className="truncate text-sm font-medium">{item.repositoryUrl || "Repository submission"}</p><p className="mt-1 text-xs text-muted-foreground">Submission {item.submissionId.slice(0, 8)}{item.execution?.id ? ` · Execution ${item.execution.id.slice(0, 8)}` : " · Execution not started"}</p></div>
            <div className="flex items-center gap-2">{item.execution ? <ExecutionStatusBadge status={item.execution.status} /> : <ExecutionStatusBadge status={item.submission?.status ?? item.status ?? "queued"} />}
              {item.execution?.id && <Button asChild variant="outline" size="sm"><Link to={`/executions/${item.execution.id}`}>Open results <ArrowRight /></Link></Button>}
            </div>
          </div>)}</div>}
        </CardContent>
      </Card>
      <BackendHealthCard {...health} />
    </div>

    <section className="grid gap-4 md:grid-cols-2">
      <Card><CardHeader><CardTitle>Required foundation</CardTitle><CardDescription>Agents 1 and 2 are always included in every analysis.</CardDescription></CardHeader><CardContent className="space-y-2 text-sm"><p>Structure Analyzer maps repository files and evidence.</p><p>Business Logic Analyst examines domain behavior and rules.</p></CardContent></Card>
      <Card><CardHeader><CardTitle>Partial results stay visible</CardTitle><CardDescription>Unavailable or skipped agents are not presented as successful.</CardDescription></CardHeader><CardContent className="text-sm leading-6 text-muted-foreground">Agent results retain lifecycle states, evidence, recommendations, uncertainty, and review provenance from the backend.</CardContent></Card>
    </section>
  </div>;
}
