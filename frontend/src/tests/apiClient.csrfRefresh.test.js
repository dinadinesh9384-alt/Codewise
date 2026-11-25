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

const CSRF_URL = "/session/csrf/";
const isCsrfRefresh = (call) => call.url.endsWith(CSRF_URL);
const isPost = (call) => call.options.method === "POST";

// Fake backend: a POST succeeds only when it carries the currently valid token.
function csrfServer({ validToken = "new-token", refreshDelay = 0, postDelay = () => 0 } = {}) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith(CSRF_URL)) {
      if (refreshDelay) await new Promise((r) => setTimeout(r, refreshDelay));
      return jsonResponse({ csrfToken: validToken });
    }
    const delay = postDelay(options);
    if (delay) await new Promise((r) => setTimeout(r, delay));
    if (options.headers["X-CSRFToken"] !== validToken) {
      return jsonResponse({ detail: "CSRF Failed: CSRF token missing or incorrect." }, 403);
    }
    return jsonResponse({ result: "ok", key: options.headers["Idempotency-Key"] });
  };
  return { calls, fetchImpl };
}

test("genuine CSRF rejection triggers a single token refresh and retries request", async () => {
  const server = csrfServer({ validToken: "new-token" });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });

  const result = await client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }, { idempotencyKey: "stable-key" });
  assert.equal(result.result, "ok");
  const { calls } = server;
  // Actual sequence: original POST (rejected), refresh GET, retry POST (accepted).
  assert.equal(calls.length, 3);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["X-CSRFToken"], "old");
  assert.equal(calls[1].options.method, "GET");
  assert.ok(isCsrfRefresh(calls[1]));
  assert.equal(calls[1].options.credentials, "include");
  assert.equal(calls[2].options.method, "POST");
  // The stale readable cookie must not override the refreshed token.
  assert.equal(calls[2].options.headers["X-CSRFToken"], "new-token");
  // Idempotency key must be unchanged and session credentials preserved.
  assert.equal(calls[0].options.headers["Idempotency-Key"], "stable-key");
  assert.equal(calls[2].options.headers["Idempotency-Key"], "stable-key");
  assert.equal(calls[0].options.credentials, "include");
  assert.equal(calls[2].options.credentials, "include");
  assert.equal(calls[2].url, calls[0].url);
  assert.deepEqual(JSON.parse(calls[2].options.body), JSON.parse(calls[0].options.body));
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
  // Two simultaneous POSTs both carry the stale token and are rejected; the refresh is slow.
  const server = csrfServer({ validToken: "new-token", refreshDelay: 10 });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });

  const p1 = client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }, { idempotencyKey: "k1" });
  const p2 = client.post("/api/v1/workspaces/def/submissions/", { repository_url: "https://github.com/c/d" }, { idempotencyKey: "k2" });
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.result, "ok");
  assert.equal(r2.result, "ok");
  // Idempotency keys are stable across the retry of each request.
  assert.equal(r1.key, "k1");
  assert.equal(r2.key, "k2");
  const { calls } = server;
  // Exactly one refresh despite two failures.
  assert.equal(calls.filter(isCsrfRefresh).length, 1);
  const posts = calls.filter(isPost);
  assert.equal(posts.length, 4); // two originals + two retries (one retry each)
  for (const key of ["k1", "k2"]) {
    const attempts = posts.filter((c) => c.options.headers["Idempotency-Key"] === key);
    assert.deepEqual(attempts.map((c) => c.options.headers["X-CSRFToken"]), ["old", "new-token"]);
  }
});

test("retry without a caller-supplied key reuses the same generated Idempotency-Key", async () => {
  const server = csrfServer({ validToken: "new-token" });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });
  await client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" });
  const posts = server.calls.filter(isPost);
  assert.equal(posts.length, 2);
  const first = posts[0].options.headers["Idempotency-Key"];
  assert.ok(first && first.length > 0);
  assert.equal(posts[1].options.headers["Idempotency-Key"], first);
});

test("a later request after a refresh uses the refreshed token, not the stale cookie", async () => {
  const server = csrfServer({ validToken: "new-token" });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });
  await client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "a" });
  server.calls.length = 0;
  await client.post("/api/v1/workspaces/abc/executions/", {}, { idempotencyKey: "b" });
  assert.equal(server.calls.length, 1);
  assert.equal(server.calls[0].options.headers["X-CSRFToken"], "new-token");
});

test("a failure that arrives after another request already refreshed retries without a second refresh", async () => {
  // p2's rejection is delayed until after p1's refresh has fully completed and cleaned up.
  const server = csrfServer({
    validToken: "new-token",
    refreshDelay: 5,
    postDelay: (options) => (options.headers["Idempotency-Key"] === "slow" && options.headers["X-CSRFToken"] === "old" ? 60 : 0),
  });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });
  const [r1, r2] = await Promise.all([
    client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "fast" }),
    client.post("/api/v1/workspaces/def/submissions/", {}, { idempotencyKey: "slow" }),
  ]);
  assert.equal(r1.result, "ok");
  assert.equal(r2.result, "ok");
  assert.equal(server.calls.filter(isCsrfRefresh).length, 1);
});

test("only one retry is made when the refreshed token is also rejected", async () => {
  const calls = [];
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=old" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith(CSRF_URL)) return jsonResponse({ csrfToken: "new-token" });
      return jsonResponse({ detail: "CSRF Failed" }, 403);
    },
  });
  await assert.rejects(
    client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "k" }),
    (err) => err instanceof ApiError && err.code === "csrf_failed" && err.status === 403
  );
  assert.equal(calls.filter(isCsrfRefresh).length, 1);
  assert.equal(calls.filter(isPost).length, 2);
});

test("a failed shared refresh rejects every waiting request safely and a later request can refresh again", async () => {
  let refreshes = 0;
  let refreshWorks = false;
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=old" },
    fetchImpl: async (url, options) => {
      if (url.endsWith(CSRF_URL)) {
        refreshes += 1;
        await new Promise((r) => setTimeout(r, 10));
        return refreshWorks ? jsonResponse({ csrfToken: "new-token" }) : jsonResponse({ detail: "Service unavailable" }, 503);
      }
      return options.headers["X-CSRFToken"] === "new-token" ? jsonResponse({ result: "ok" }) : jsonResponse({ detail: "CSRF Failed" }, 403);
    },
  });
  const settled = await Promise.allSettled([
    client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "k1" }),
    client.post("/api/v1/workspaces/def/submissions/", {}, { idempotencyKey: "k2" }),
  ]);
  assert.equal(refreshes, 1);
  for (const item of settled) {
    assert.equal(item.status, "rejected");
    assert.ok(item.reason instanceof ApiError);
    assert.equal(item.reason.code, "internal_error");
  }
  // The shared slot was cleaned up, so a later request can start a fresh refresh.
  refreshWorks = true;
  const ok = await client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "k3" });
  assert.equal(ok.result, "ok");
  assert.equal(refreshes, 2);
});

test("workspace_access_denied 403 is a permission error and is not retried", async () => {
  const denied = [];
  const client = createApiClient({
    documentRef: { cookie: "csrftoken=x" },
    fetchImpl: async (url, options) => { denied.push({ url, options }); return jsonResponse({ error: "workspace_access_denied" }, 403); },
  });
  await assert.rejects(
    client.post("/api/v1/workspaces/abc/submissions/", {}),
    (err) => err instanceof ApiError && err.code === "workspace_access_denied"
  );
  assert.equal(denied.length, 1);
});

test("CSRF tokens and request headers are never written to the console", async (t) => {
  const methods = ["log", "info", "debug", "warn", "error"];
  const spies = methods.map((name) => t.mock.method(console, name, () => {}));
  const server = csrfServer({ validToken: "new-token" });
  const client = createApiClient({ documentRef: { cookie: "csrftoken=old" }, fetchImpl: server.fetchImpl });
  await client.post("/api/v1/workspaces/abc/submissions/", {}, { idempotencyKey: "secret-key" });
  const output = JSON.stringify(spies.flatMap((spy) => spy.mock.calls.map((call) => call.arguments)));
  for (const secret of ["old", "new-token", "X-CSRFToken", "secret-key"]) {
    assert.equal(output.includes(secret), false, `console output leaked ${secret}`);
  }
});
