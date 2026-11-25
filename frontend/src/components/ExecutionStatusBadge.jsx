import { Badge } from "@/components/ui/badge.jsx";
import { statusDisplay } from "@/services/executionViewModel.js";

const TONES = {
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
  active: "border-sky-500/30 bg-sky-500/10 text-sky-500",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-500",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
  muted: "border-border bg-muted/50 text-muted-foreground",
};

export default function ExecutionStatusBadge({ status, className = "" }) {
  const descriptor = statusDisplay(status);
  return <Badge variant="outline" className={`${TONES[descriptor.tone] ?? TONES.muted} ${className}`}>{descriptor.label}</Badge>;
}
