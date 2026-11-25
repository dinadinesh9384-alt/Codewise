import { useCallback, useEffect, useState } from "react";
import { getBackendHealth } from "../services/backendHealth.js";

const REQUEST_TIMEOUT_MS = 10_000;

export default function useBackendHealth() {
  const [attempt, setAttempt] = useState(0);
  const [health, setHealth] = useState({
    status: "loading",
    checkedAt: null,
    message: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;
    const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    getBackendHealth({ signal: controller.signal })
      .then(() => {
        if (isCurrent) {
          setHealth({ status: "operational", checkedAt: new Date(), message: null });
        }
      })
      .catch(() => {
        if (!isCurrent) return;
        const timedOut = controller.signal.aborted;
        setHealth({
          status: "unavailable",
          checkedAt: new Date(),
          message: timedOut
            ? "The health check timed out. Make sure the API is running, then try again."
            : "The API could not be reached. Make sure the backend is running, then try again.",
        });
      })
      .finally(() => window.clearTimeout(timeoutId));

    return () => {
      isCurrent = false;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setHealth((current) => ({ ...current, status: "loading", message: null }));
    setAttempt((current) => current + 1);
  }, []);

  return { ...health, retry };
}