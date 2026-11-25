import { useState } from "react";
import { Check, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { getStoredWorkspaceId, setStoredWorkspaceId } from "@/services/repositoryWorkflow.js";
import { useSession } from "@/state/session-context.js";

export default function WorkspaceIdPicker({ value, onChange, compact = false }) {
  const { workspaces } = useSession();
  const [draft, setDraft] = useState(value ?? getStoredWorkspaceId());
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const save = (event) => {
    event.preventDefault();
    const selected = workspaces.find((workspace) => workspace.id === draft);
    if (!selected) { setMessage("Choose one of your available workspaces."); return; }
    try {
      setStoredWorkspaceId(selected.id);
      onChange?.(selected.id);
      setError(""); setMessage(`${selected.name} selected (${selected.role}).`);
    } catch (requestError) { setError(requestError.message); }
  };

  return <form onSubmit={save} className={compact ? "space-y-2" : "space-y-3"}>
    <label htmlFor="workspace-id" className="flex items-center gap-2 text-sm font-medium"><KeyRound className="size-4 text-primary" />Workspace</label>
    <div className="flex flex-col gap-2 sm:flex-row">
      <select id="workspace-id" value={draft} onChange={(event) => { setDraft(event.target.value); setMessage(""); setError(""); }}
        className="flex h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-describedby="workspace-picker-message">
        <option value="">Choose an accessible workspace</option>
        {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
      </select>
      <Button type="submit" variant="outline" className="shrink-0 gap-2" disabled={!draft}><Check className="size-4" />Select workspace</Button>
    </div>
    <p id="workspace-picker-message" className={`text-xs leading-5 ${error || (message && !message.includes("selected")) ? "text-destructive" : "text-muted-foreground"}`} role={message || error ? "status" : undefined}>
      {error || message || (workspaces.length ? "Workspaces are loaded from your authenticated Django memberships." : "Your account has no workspace memberships. Ask an owner to add you.")}
    </p>
  </form>;
}
