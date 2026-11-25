import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../services/apiClient.js";

const jsonResponse = (data, status = 200) => ({ status, ok: status >= 200 && status < 300,
  headers: { get: () => "application/json" }, json: async () => data });

test("POST bootstraps CSRF, sends session credentials and an idempotency key", async () => {
  const calls = [];
  const client = createApiClient({ documentRef: { cookie: "" }, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/session/csrf/")) return jsonResponse({ csrfToken: "masked-token-from-json" });
    return jsonResponse({ result: "ok" });
  } });
  await client.post("/api/v1/workspaces/abc/submissions/", { repository_url: "https://github.com/a/b" }, { idempotencyKey: "stable-key" });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.credentials, "include");
  assert.equal(calls[1].options.headers["X-CSRFToken"], "masked-token-from-json");
  assert.equal(calls[1].options.headers["Idempotency-Key"], "stable-key");
  assert.equal(calls[1].options.credentials, "include");
});

test("expired session becomes a safe authentication error", async () => {
  const client = createApiClient({ documentRef: { cookie: "csrftoken=x" }, fetchImpl: async () => jsonResponse({ detail: "Authentication credentials were not provided." }, 401) });
  await assert.rejects(client.get("/api/v1/workspaces/x/submissions/"), (error) => error instanceof ApiError && error.code === "authentication_required");
});

test("login response rotates the in-memory CSRF token used by later session POSTs", async () => {
  const calls = [];
  const client = createApiClient({ documentRef: { cookie: "" }, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/session/csrf/")) return jsonResponse({ csrfToken: "pre-login-token" });
    if (url.endsWith("/session/login/")) return jsonResponse({ is_authenticated: true, csrfToken: "rotated-login-token" });
    return jsonResponse({ is_authenticated: false });
  } });
  await client.post("/api/v1/session/login/", { username: "person", password: "pass" });
  await client.post("/api/v1/session/logout/", {});
  assert.equal(calls.find((call) => call.url.endsWith("/session/logout/")).options.headers["X-CSRFToken"], "rotated-login-token");
});

test("CSRF rejection is distinguished and malformed API paths or bodies are rejected", async () => {
  const client = createApiClient({ documentRef: { cookie: "csrftoken=x" }, fetchImpl: async () => jsonResponse({ detail: "CSRF Failed: token missing" }, 403) });
  await assert.rejects(client.post("/api/v1/workspaces/x/submissions/", {}), (error) => error.code === "csrf_failed");
  await assert.rejects(client.get("https://example.com/api/v1/health/"), TypeError);
  await assert.rejects(client.post("/api/v1/x/", []), TypeError);
});

test("API server errors do not expose raw response data as a success", async () => {
  const client = createApiClient({ documentRef: { cookie: "" }, fetchImpl: async () => jsonResponse({ error: "internal_error" }, 500) });
  await assert.rejects(client.get("/api/v1/health/"), (error) => error instanceof ApiError && error.code === "internal_error");
});
