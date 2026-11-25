import { Activity, CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS = {
  loading: { label: "Checking connection", detail: "Contacting the backend health endpoint…", icon: LoaderCircle, tone: "text-amber-500" },
  operational: { label: "Operational", detail: "The backend responded successfully.", icon: CircleCheck, tone: "text-emerald-500" },
  unavailable: { label: "Unavailable", detail: "The API could not confirm availability.", icon: CircleAlert, tone: "text-rose-500" },
};

export default function BackendHealthCard({ status, checkedAt, message, retry }) {
  const current = STATUS[status] ?? STATUS.unavailable;
  const StatusIcon = current.icon;
  const isLoading = status === "loading";

  return (
    <Card className="h-full border-border/70" aria-busy={isLoading}>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="flex items-center gap-3"><span className={`flex size-9 items-center justify-center rounded-lg bg-muted ${current.tone}`}><StatusIcon className={`size-4 ${isLoading ? "animate-spin" : ""}`} aria-hidden="true" /></span><div><p className="text-xs text-muted-foreground">Service check</p><CardTitle className="mt-0.5">Backend API</CardTitle></div></div>
        <Badge variant="outline" className="font-mono text-[10px]">v1</Badge>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2"><p className={`text-lg font-semibold ${current.tone}`} role="status" aria-live="polite">{current.label}</p><Activity className="size-3.5 text-muted-foreground" /></div>
        <p className="mt-1 text-sm leading-5 text-muted-foreground">{status === "unavailable" ? message : current.detail}</p>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-4">
          <p className="text-[11px] text-muted-foreground">{checkedAt ? `Checked ${checkedAt.toLocaleTimeString()}` : "Checks when the overview opens"}</p>
          {status === "unavailable" && <Button size="sm" variant="outline" onClick={retry} disabled={isLoading}>Try again</Button>}
        </div>
      </CardContent>
    </Card>
  );
}
