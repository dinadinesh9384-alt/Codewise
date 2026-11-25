import { useCallback, useEffect, useState } from "react";
import { ArrowRight, LoaderCircle, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import PageHeading from "@/components/PageHeading.jsx";
import WorkspaceIdPicker from "@/components/WorkspaceIdPicker.jsx";
import ExecutionStatusBadge from "@/components/ExecutionStatusBadge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { getExecution, getStoredWorkspaceId, getSubmission, readRecentActivity } from "@/services/repositoryWorkflow.js";

export default function HistoryPage() {
  const [workspaceId, setWorkspaceId] = useState(getStoredWorkspaceId());
  const [records, setRecords] = useState([]);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(async () => {
    const saved = readRecentActivity().filter((item) => item.workspaceId === workspaceId);
    if (!workspaceId || !saved.length) { setRecords([]); setState("ready"); return; }
    setState("loading"); setError("");
    const rows = await Promise.all(saved.map(async (item) => {
      try {
        const submission = (await getSubmission(workspaceId, item.submissionId)).submission;
        const execution = item.executionId ? (await getExecution(workspaceId, item.executionId)).execution : null;
        return { ...item, submission, execution };
      } catch (requestError) { return { ...item, error: requestError.message }; }
    }));
    setRecords(rows); setError(rows.every((item) => item.error) ? rows[0]?.error ?? "Unable to load history." : ""); setState("ready");
  }, [workspaceId]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- Route entry intentionally starts an API refresh.
  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  return <div className="mx-auto max-w-6xl space-y-6">
    <PageHeading eyebrow="Workspace" title="Analysis history" description="Refreshes submissions and executions saved by this browser against the authenticated API." action={<Button asChild><Link to="/repositories">Analyze repository <ArrowRight /></Link></Button>} />
    <Card><CardHeader><CardTitle>Workspace</CardTitle><CardDescription>History is browser-local because the API does not expose a workspace list endpoint.</CardDescription></CardHeader><CardContent><WorkspaceIdPicker value={workspaceId} onChange={(value) => { setWorkspaceId(value); setRefreshKey((key) => key + 1); }} /></CardContent></Card>
    <Card><CardHeader className="flex-row items-center justify-between"><div><CardTitle>Saved analyses</CardTitle><CardDescription>Only entries created in this browser appear here.</CardDescription></div><Button variant="outline" size="sm" onClick={() => setRefreshKey((key) => key + 1)} disabled={state === "loading"}><RefreshCw className={state === "loading" ? "animate-spin" : ""} />Refresh</Button></CardHeader><CardContent>
      {state === "loading" && <p role="status" className="flex justify-center gap-2 py-8 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Refreshing from API</p>}
      {error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
      {state === "ready" && !records.length && <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No saved analyses for this workspace. Submit a repository to begin.</div>}
      {state === "ready" && records.length > 0 && <div className="divide-y divide-border">{records.map((item) => <div key={item.submissionId} className="flex flex-wrap items-center justify-between gap-3 py-4 first:pt-0 last:pb-0"><div className="min-w-0"><p className="truncate text-sm font-medium">{item.repositoryUrl || "Repository submission"}</p><p className="mt-1 text-xs text-muted-foreground">Submission {item.submissionId.slice(0, 8)} · {item.submission?.status ?? item.status ?? "Status unavailable"}{item.executionId ? ` · execution ${item.executionId.slice(0, 8)}` : " · execution not started"}</p>{item.error && <p className="mt-1 text-xs text-destructive">{item.error}</p>}</div><div className="flex items-center gap-2">{item.execution ? <ExecutionStatusBadge status={item.execution.status} /> : <ExecutionStatusBadge status={item.submission?.status ?? item.status ?? "unavailable"} />}{item.executionId && <Button asChild size="sm" variant="outline"><Link to={`/executions/${item.executionId}`}>Open results <ArrowRight /></Link></Button>}</div></div>)}</div>}
    </CardContent></Card>
  </div>;
}
