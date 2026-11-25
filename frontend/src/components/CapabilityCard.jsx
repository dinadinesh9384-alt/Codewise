import * as m from "motion/react-m";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function CapabilityCard({ icon: Icon, title, description, index }) {
  return (
    <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.045, duration: 0.3 }} whileHover={{ y: -2 }}>
      <Card className="h-full border-border/70 transition-colors hover:border-primary/30">
        <CardHeader className="flex-row items-start justify-between gap-3">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary"><Icon aria-hidden="true" className="size-5" /></span>
          <Badge variant="outline" className="border-border/80 text-muted-foreground">Planned</Badge>
        </CardHeader>
        <CardContent>
          <CardTitle>{title}</CardTitle>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
        </CardContent>
      </Card>
    </m.div>
  );
}
