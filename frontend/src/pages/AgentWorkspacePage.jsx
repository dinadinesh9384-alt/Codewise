import { useCallback, useEffect, useState } from "react";
import { ArrowRight, LoaderCircle } from "lucide-react";
import { Link } from "react-router-dom";
import PageHeading from "@/components/PageHeading.jsx";
import WorkspaceIdPicker from "@/components/WorkspaceIdPicker.jsx";
import ExecutionStatusBadge from "@/components/ExecutionStatusBadge.jsx";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { getExecution, getStoredWorkspaceId, getAgentResults, readRecentActivity } from "@/services/repositoryWorkflow.js";
import { buildExecutionAgents, OFFICIAL_AGENTS } from "@/services/executionViewModel.js";

export default function AgentWorkspacePage() {
  const [workspaceId, setWorkspaceId] = useState(getStoredWorkspaceId());
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const saved = readRecentActivity().filter((item) => item.workspaceId === workspaceId && item.executionId);
    if (!workspaceId || !saved.length) { setRuns([]); return; }
    setLoading(true); setError("");
    try {
      const loaded = await Promise.all(saved.map(async (item) => {
        const [executionResponse, resultResponse] = await Promise.all([getExecution(workspaceId, item.executionId), getAgentResults(workspaceId, item.executionId)]);
        return { ...item, execution: executionResponse.execution, agents: buildExecutionAgents(executionResponse.execution, resultResponse) };
      }));
      setRuns(loaded);
    } catch (requestError) { setError(requestError.message || "Agent results could not be loaded."); setRuns([]); }
    finally { setLoading(false); }
  }, [workspaceId]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- Route entry intentionally starts an API refresh.
  useEffect(() => { refresh(); }, [refresh]);

  return <div className="mx-auto max-w-6xl space-y-6"><PageHeading eyebrow="Intelligence" title="Agent workspace" description="Browse structured outputs for the eight official agents across this browser’s saved executions." />
    <Card><CardHeader><CardTitle>Workspace</CardTitle><CardDescription>Choose the workspace associated with your Django session.</CardDescription></CardHeader><CardContent><WorkspaceIdPicker value={workspaceId} onChange={setWorkspaceId} /></CardContent></Card>
    {loading && <p role="status" className="flex justify-center gap-2 py-8 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />Loading agent results</p>}
    {error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
    {!loading && !error && runs.length === 0 && <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">No saved execution results for this workspace. Submit and run an analysis first.</CardContent></Card>}
    {runs.map((run) => <Card key={run.execution.id}><CardHeader className="flex-row items-center justify-between"><div><CardTitle className="text-base">{run.repositoryUrl || "Repository analysis"}</CardTitle><CardDescription>Execution {run.execution.id.slice(0, 12)}</CardDescription></div><ExecutionStatusBadge status={run.execution.status} /></CardHeader><CardContent className="grid gap-3 sm:grid-cols-2">{run.agents.filter((agent) => agent.selected || agent.status !== "unavailable").map((agent) => <div key={agent.id} className="flex items-center justify-between gap-3 rounded-lg border border-border/70 p-3"><div><p className="text-sm font-medium">{OFFICIAL_AGENTS.findIndex((item) => item.id === agent.id) + 1}. {agent.name}</p><p className="mt-1 text-xs text-muted-foreground">{agent.selected ? "Selected" : "Not selected"}</p></div><div className="flex items-center gap-2"><ExecutionStatusBadge status={agent.status} /><Button asChild size="icon" variant="ghost" aria-label={`Open ${agent.name} result`}><Link to={`/executions/${run.execution.id}/agents/${agent.id}`}><ArrowRight /></Link></Button></div></div>)}</CardContent></Card>)}
  </div>;
}
