// CSRF refresh and retry tests
import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../services/apiClient.js";

// Helper to make a json response object similar to existing tests.
const jsonResponse = (data, status = 200) => ({
  status,
  ok: status >= 200 && status < 300,
  headers: { get: () => "application/json" },
  json: async () => data,
});

test("genuine CSRF rejection triggers a single token refresh and retries request", async () => {
  const calls = [];
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=old" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith("/session/csrf/")) {
        // Return fresh token
        return jsonResponse({ csrfToken: "new-token" });
      }
      // First POST attempt returns CSRF failure
      if (!calls.some(c => c.url.endsWith("/session/csrf/"))) {
        return jsonResponse({ detail: "CSRF Failed: token missing" }, 403);
      }
      // After refresh, succeed
      return jsonResponse({ result: "ok" });
    },
  });

  const result = await client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }, { idempotencyKey: "stable-key" });
  assert.equal(result.result, "ok");
  // Expect three calls: CSRF refresh, original POST (failed), retry POST (succeeded)
  assert.equal(calls.length, 3);
  // Second call should be the POST that got 403
  const postCall1 = calls[1];
  assert.equal(postCall1.options.headers["X-CSRFToken"], "old");
  // Third call is the retry with refreshed token
  const postCall2 = calls[2];
  assert.equal(postCall2.options.headers["X-CSRFToken"], "new-token");
  // Idempotency key must be unchanged
  assert.equal(postCall2.options.headers["Idempotency-Key"], "stable-key");
});

test("permission‑related 403 is not retried", async () => {
  const calls = [];
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=x" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return jsonResponse({ detail: "You do not have permission." }, 403);
    },
  });

  await assert.rejects(
    client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }),
    (err) => err instanceof ApiError && err.code !== "csrf_failed"
  );
  // Only one request made – no retry
  assert.equal(calls.length, 1);
});

test("failed CSRF refresh propagates safe error", async () => {
  const calls = [];
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=old" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith("/session/csrf/")) {
        // Simulate network error on refresh
        return jsonResponse({ detail: "Service unavailable" }, 503);
      }
      // First POST returns CSRF failure
      return jsonResponse({ detail: "CSRF Failed" }, 403);
    },
  });

  await assert.rejects(
    client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }),
    (err) => err instanceof ApiError && err.code === "internal_error"
  );
  // Two calls: initial POST and CSRF refresh attempt
  assert.equal(calls.length, 2);
});

test("concurrent CSRF failures share a single refresh", async () => {
  const calls = [];
  // Simulate two simultaneous POSTs that both get a CSRF 403.
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=old" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith("/session/csrf/")) {
        // Return fresh token after a short delay
        await new Promise((r) => setTimeout(r, 10));
        return jsonResponse({ csrfToken: "new-token" });
      }
      // Both POST attempts initially get 403
      return jsonResponse({ detail: "CSRF Failed" }, 403);
    },
  });

  const p1 = client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }, { idempotencyKey: "k1" });
  const p2 = client.post("/api/v1/workspaces/def/submissions/", { repository_url: "https://github.com/c/d" }, { idempotencyKey: "k2" });
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.result, "ok");
  assert.equal(r2.result, "ok");
  // Expect exactly one CSRF refresh call despite two requests.
  const csrfCalls = calls.filter(c => c.url.endsWith("/session/csrf/"));
  assert.equal(csrfCalls.length, 1);
});
