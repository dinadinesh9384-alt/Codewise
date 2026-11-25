import { ArrowLeft, CircleDashed } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import PageHeading from "@/components/PageHeading.jsx";

export default function PlannedPage({ title, description }) {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-9">
      <PageHeading eyebrow="Workspace" title={title} description={description} />
      <Card className="border-dashed border-border/80 bg-card/70">
        <CardContent className="flex flex-col items-center px-6 py-14 text-center sm:py-20">
          <span className="mb-5 flex size-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground"><CircleDashed className="size-6" /></span>
          <Badge variant="secondary">Planned</Badge>
          <h2 className="mt-4 text-lg font-semibold">This area is not available yet</h2>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">This navigation destination is reserved for the CodeWise workspace roadmap. No feature is running here yet.</p>
          <Button asChild variant="outline" className="mt-6"><Link to="/"><ArrowLeft />Back to overview</Link></Button>
        </CardContent>
      </Card>
    </div>
  );
}
