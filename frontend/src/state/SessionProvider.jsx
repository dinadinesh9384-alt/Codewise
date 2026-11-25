import { useCallback, useEffect, useMemo, useState } from "react";
import { getSession, getWorkspaces, login as loginRequest, logout as logoutRequest } from "@/services/session.js";
import { SessionContext } from "@/state/session-context.js";

export function SessionProvider({ children }) {
  const [session, setSession] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const current = await getSession();
      if (!current?.is_authenticated) { setSession(null); setWorkspaces([]); return null; }
      setSession(current);
      const workspaceResponse = await getWorkspaces();
      setWorkspaces(workspaceResponse?.workspaces ?? []);
      return current;
    } catch (requestError) {
      setSession(null); setWorkspaces([]); setError(requestError.message || "Could not check your Django session.");
      return null;
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Mount performs the session bootstrap request.
    refresh();
  }, [refresh]);

  const signIn = useCallback(async (credentials) => {
    setError("");
    try {
      const current = await loginRequest(credentials);
      setSession(current);
      const workspaceResponse = await getWorkspaces();
      setWorkspaces(workspaceResponse?.workspaces ?? []);
      return current;
    } catch (requestError) {
      setError(requestError.message || "Sign-in failed.");
      throw requestError;
    }
  }, []);

  const signOut = useCallback(async () => {
    setError("");
    try {
      await logoutRequest();
      setSession(null); setWorkspaces([]);
    } catch (requestError) {
      setError(requestError.message || "Sign-out failed.");
      throw requestError;
    }
  }, []);

  const value = useMemo(() => ({ session, workspaces, loading, error, refresh, signIn, signOut }),
    [session, workspaces, loading, error, refresh, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

