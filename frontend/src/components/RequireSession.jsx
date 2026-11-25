import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "@/state/session-context.js";

export default function RequireSession({ children }) {
  const { session, loading, error } = useSession();
  const location = useLocation();
  if (loading) return <div role="status" className="grid min-h-screen place-content-center text-sm text-muted-foreground">Checking Django session...</div>;
  if (!session?.is_authenticated) return <Navigate to="/login" replace state={{ from: location.pathname, sessionError: error }} />;
  return children;
}
