import { useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ArrowRight, LoaderCircle, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button.jsx";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card.jsx";
import { Input } from "@/components/ui/input.jsx";
import { useSession } from "@/state/session-context.js";

export default function LoginPage() {
  const { session, loading, error: sessionError, signIn } = useSession();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (session?.is_authenticated) return <Navigate to={location.state?.from || "/"} replace />;

  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError("");
    try { await signIn({ username, password }); }
    catch (requestError) { setError(requestError.message || "Sign-in failed."); }
    finally { setBusy(false); }
  };

  return <main className="grid min-h-screen place-items-center bg-background px-4 py-10">
    <Card className="w-full max-w-md border-border/80 shadow-xl shadow-black/10">
      <CardHeader className="space-y-3"><div className="flex size-11 items-center justify-center rounded-xl bg-primary/10 text-primary"><ShieldCheck className="size-5" /></div><CardTitle className="text-2xl">Sign in to CodeWise</CardTitle><CardDescription>Use your existing CodeWise Django account. Accounts are provisioned by your workspace administrator.</CardDescription></CardHeader>
      <CardContent><form className="space-y-4" onSubmit={submit}>
        <div className="space-y-2"><label htmlFor="username" className="text-sm font-medium">Username</label><Input id="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required maxLength={150} /></div>
        <div className="space-y-2"><label htmlFor="password" className="text-sm font-medium">Password</label><Input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={1024} /></div>
        {(error || sessionError || location.state?.sessionError) && <p role="alert" className="rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">{error || sessionError || location.state.sessionError}</p>}
        <Button type="submit" className="w-full" disabled={busy || loading}>{busy ? <><LoaderCircle className="animate-spin" />Signing in</> : <>Sign in <ArrowRight /></>}</Button>
        <p className="text-xs leading-5 text-muted-foreground">Session cookies are HttpOnly and protected by Django CSRF checks. CodeWise does not store your password in browser storage.</p>
      </form></CardContent>
    </Card>
  </main>;
}
