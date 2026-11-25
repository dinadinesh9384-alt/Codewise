import { Button } from "@/components/ui/button";
import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return <section className="mx-auto max-w-xl py-20 text-center"><p className="text-sm font-medium text-primary">404</p><h1 className="mt-2 text-3xl font-semibold">Page not found</h1><p className="mt-3 text-muted-foreground">That address does not match a CodeWise page.</p><Button asChild className="mt-6"><Link to="/">Go to overview</Link></Button></section>;
}
