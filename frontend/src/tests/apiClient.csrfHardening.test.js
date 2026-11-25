// CSRF hardening tests: (A) JSON-only CSRF classification, (B) caller-specific abort while waiting
// on the shared CSRF refresh. Existing CSRF tests live in apiClient.csrfRefresh.test.js.
import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../services/apiClient.js";

const CSRF_URL = "/session/csrf/";
const SUBMIT = "/api/v1/workspaces/w1/submissions/";
const isRefresh = (call) => call.url.endsWith(CSRF_URL);
const isPost = (call) => call.options.method === "POST";

const jsonResponse = (data, status = 200) => ({
  status,
  ok: status >= 200 && status < 300,
  headers: { get: () => "application/json" },
  json: async () => data,
});

// What an intermediary (proxy / WAF / CDN) or an unconfigured Django returns: HTML, no JSON body.
const htmlResponse = (status, body = "<html><body><h1>Forbidden</h1></body></html>") => ({
  status,
  ok: false,
  headers: { get: () => "text/html; charset=utf-8" },
  json: async () => { throw new SyntaxError("Unexpected token <"); },
  text: async () => body,
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tick = async (times = 5) => { for (let i = 0; i < times; i += 1) await new Promise((r) => setImmediate(r)); };
async function until(condition, label = "condition") {
  for (let i = 0; i < 200; i += 1) {
    if (condition()) return;
    await new Promise((r) => setImmediate(r));
  }
  assert.fail(`timed out waiting for ${label}`);
}

// Resolves with how a promise looks after `ms`: still pending, or already settled.
async function settledWithin(promise, ms = 100) {
  const pending = Symbol("pending");
  const outcome = await Promise.race([
    promise.then((value) => ({ state: "resolved", value }), (error) => ({ state: "rejected", error })),
    sleep(ms).then(() => pending),
  ]);
  return outcome === pending ? { state: "pending" } : outcome;
}

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function trackUnhandled(t) {
  const seen = [];
  const handler = (reason) => seen.push(reason);
  process.on("unhandledRejection", handler);
  t.after(() => process.removeListener("unhandledRejection", handler));
  return async () => {
    await tick(8);
    assert.deepEqual(seen.map(String), [], "no unhandled promise rejections");
  };
}

// Fake backend. A POST succeeds only with the currently valid token, otherwise JSON csrf_failed.
// The refresh can be held open, released or failed from the test. Like real fetch, an
// already-aborted signal rejects immediately with AbortError.
function server({ validToken = "new-token", rejectResponse } = {}) {
  const calls = [];
  const state = { validToken, gate: null, refreshCount: 0 };
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    if (options.signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    if (url.endsWith(CSRF_URL)) {
      state.refreshCount += 1;
      if (state.gate) await state.gate.promise; // a rejection surfaces as a network failure
      return jsonResponse({ csrfToken: state.validToken });
    }
    if (rejectResponse) return rejectResponse(url, options, calls);
    if (options.headers["X-CSRFToken"] !== state.validToken) return jsonResponse({ error: "csrf_failed" }, 403);
    return jsonResponse({ result: "ok", key: options.headers["Idempotency-Key"], token: options.headers["X-CSRFToken"] });
  };
  return {
    calls,
    state,
    fetchImpl,
    holdRefresh() { state.gate = deferred(); return state.gate; },
    posts: () => calls.filter(isPost),
    refreshes: () => calls.filter(isRefresh),
  };
}

const staleCookie = { cookie: "csrftoken=old-token" };
const noCookie = { cookie: "" };

// ---------------------------------------------------------------------------------------------
// Part A: classification
// ---------------------------------------------------------------------------------------------

test("A: genuine JSON csrf_failed 403 refreshes and retries exactly once with the refreshed token and original key", async () => {
  const srv = server();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });

  const result = await client.post(SUBMIT, { repository_url: "https://github.com/o/r" });

  assert.equal(result.result, "ok");
  assert.deepEqual(srv.calls.map((c) => (isRefresh(c) ? "refresh" : c.options.method)), ["POST", "refresh", "POST"]);
  const [first, , retry] = srv.calls;
  assert.equal(first.options.headers["X-CSRFToken"], "old-token");
  assert.equal(retry.options.headers["X-CSRFToken"], "new-token");
  assert.ok(first.options.headers["Idempotency-Key"]);
  assert.equal(retry.options.headers["Idempotency-Key"], first.options.headers["Idempotency-Key"]);
  assert.equal(retry.options.credentials, "include");
});

test("A: a caller-supplied idempotency key is reused unchanged on the retry", async () => {
  const srv = server();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  await client.post(SUBMIT, {}, { idempotencyKey: "caller-key-1" });
  assert.deepEqual(srv.posts().map((c) => c.options.headers["Idempotency-Key"]), ["caller-key-1", "caller-key-1"]);
});

test("A: the one-retry limit holds when the refreshed token is also rejected", async () => {
  const srv = server({ rejectResponse: () => jsonResponse({ error: "csrf_failed" }, 403) });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });

  await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === "csrf_failed" && error.status === 403);

  assert.equal(srv.posts().length, 2);
  assert.equal(srv.refreshes().length, 1);
});

test("A: a generic HTML 403 is a generic forbidden error: no refresh and no retry", async () => {
  const srv = server({ rejectResponse: () => htmlResponse(403) });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });

  await assert.rejects(client.post(SUBMIT, {}), (error) => {
    assert.equal(error.name, "ApiError");
    assert.equal(error.status, 403);
    assert.equal(error.code, "forbidden");
    assert.notEqual(error.code, "csrf_failed");
    assert.doesNotMatch(error.message, /security token/i);
    return true;
  });

  assert.equal(srv.posts().length, 1);
  assert.equal(srv.refreshes().length, 0);
});

test("A: a generic HTML 403 on a GET is forbidden and is not retried", async () => {
  const srv = server({ rejectResponse: () => htmlResponse(403) });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  await assert.rejects(client.get("/api/v1/workspaces/"), (error) => error.code === "forbidden");
  assert.equal(srv.calls.length, 1);
});

test("A: an HTML 403 on the retry after a genuine CSRF failure stops there (no second refresh)", async () => {
  let n = 0;
  const srv = server({ rejectResponse: () => { n += 1; return n === 1 ? jsonResponse({ error: "csrf_failed" }, 403) : htmlResponse(403); } });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });

  await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === "forbidden");

  assert.deepEqual(srv.calls.map((c) => (isRefresh(c) ? "refresh" : c.options.method)), ["POST", "refresh", "POST"]);
});

test("A: an HTML 403 from the CSRF bootstrap is forbidden and the request is never sent", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => { calls.push({ url, options }); return htmlResponse(403); };
  const client = createApiClient({ fetchImpl, documentRef: noCookie });

  await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === "forbidden");

  assert.equal(calls.length, 1);
  assert.ok(isRefresh(calls[0]));
});

test("A: JSON permission errors are not retried and keep their own codes", async () => {
  const cases = [
    [{ error: "workspace_access_denied" }, "workspace_access_denied"],
    [{ detail: "You do not have permission to perform this action." }, "authentication_required"],
    [{ detail: "Authentication credentials were not provided." }, "authentication_required"],
  ];
  for (const [body, code] of cases) {
    const srv = server({ rejectResponse: () => jsonResponse(body, 403) });
    const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
    await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === code && error.status === 403, JSON.stringify(body));
    assert.equal(srv.posts().length, 1, JSON.stringify(body));
    assert.equal(srv.refreshes().length, 0, JSON.stringify(body));
  }
});

test("A: legacy DRF detail 'CSRF Failed' is still recognised and retried once", async () => {
  let n = 0;
  const srv = server({ rejectResponse: (url, options) => {
    n += 1;
    return n === 1 ? jsonResponse({ detail: "CSRF Failed: CSRF token missing." }, 403)
      : jsonResponse({ result: "ok", key: options.headers["Idempotency-Key"] });
  } });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  assert.equal((await client.post(SUBMIT, {})).result, "ok");
  assert.equal(srv.refreshes().length, 1);
});

test("A: a non-JSON 401 stays authentication_required and is not retried", async () => {
  const srv = server({ rejectResponse: () => htmlResponse(401) });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === "authentication_required");
  assert.equal(srv.calls.length, 1);
});

test("A: other non-JSON failures stay invalid_response", async () => {
  const srv = server({ rejectResponse: () => htmlResponse(502, "<html>Bad gateway</html>") });
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  await assert.rejects(client.post(SUBMIT, {}), (error) => error.code === "invalid_response" && error.status === 502);
  assert.equal(srv.calls.length, 1);
});

// ---------------------------------------------------------------------------------------------
// Part B: abort handling while waiting on the shared refresh
// ---------------------------------------------------------------------------------------------

test("B: an aborted waiter rejects immediately with AbortError while the shared refresh stays active; other waiters complete", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  const srv = server();
  const gate = srv.holdRefresh();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  const abortA = new AbortController();

  const a = client.post(SUBMIT, { n: "a" }, { signal: abortA.signal });
  const b = client.post(SUBMIT, { n: "b" });
  const c = client.post(SUBMIT, { n: "c" });
  const aOutcome = settledWithin(a, 1000);
  await until(() => srv.posts().length === 3 && srv.state.refreshCount === 1, "three 403s and one refresh");
  await tick();

  abortA.abort();

  const outcome = await aOutcome;
  assert.equal(outcome.state, "rejected", "A leaves before the refresh finishes");
  assert.equal(outcome.error.name, "AbortError");
  assert.equal(srv.state.refreshCount, 1);
  assert.equal(srv.refreshes()[0].options.signal, undefined, "the shared refresh carries no caller signal");

  // The shared refresh is still pending and the other waiters are still waiting on it.
  assert.equal((await settledWithin(b, 50)).state, "pending");
  assert.equal((await settledWithin(c, 50)).state, "pending");

  gate.resolve();
  const [resultB, resultC] = await Promise.all([b, c]);
  assert.equal(resultB.result, "ok");
  assert.equal(resultC.result, "ok");
  assert.equal(resultB.token, "new-token");
  assert.equal(resultC.token, "new-token");
  assert.equal(srv.refreshes().length, 1, "still exactly one refresh");
  assert.equal(srv.posts().length, 5, "A sent no retry; B and C each retried once");
  for (const call of srv.posts().slice(0, 3)) assert.equal(call.options.headers["X-CSRFToken"], "old-token");
  // Waiters keep their own original idempotency keys across the retry.
  const keysByBody = new Map();
  for (const call of srv.posts()) {
    const body = JSON.parse(call.options.body).n;
    keysByBody.set(body, [...(keysByBody.get(body) ?? []), call.options.headers["Idempotency-Key"]]);
  }
  assert.equal(keysByBody.get("a").length, 1);
  for (const who of ["b", "c"]) {
    assert.equal(keysByBody.get(who).length, 2);
    assert.equal(keysByBody.get(who)[0], keysByBody.get(who)[1]);
  }
  await assertNoUnhandled();
});

test("B: cancelling during the initial token bootstrap rejects the caller only; the bootstrap and other waiters continue", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  const srv = server();
  const gate = srv.holdRefresh();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: noCookie });
  const abortA = new AbortController();

  const a = client.post(SUBMIT, { n: "a" }, { signal: abortA.signal });
  const b = client.post(SUBMIT, { n: "b" });
  const aOutcome = settledWithin(a, 1000);
  await until(() => srv.state.refreshCount === 1, "bootstrap refresh");
  await tick();
  assert.equal(srv.posts().length, 0, "nothing is sent before a token exists");

  abortA.abort();

  const outcome = await aOutcome;
  assert.equal(outcome.state, "rejected");
  assert.equal(outcome.error.name, "AbortError");
  assert.equal((await settledWithin(b, 50)).state, "pending");

  gate.resolve();
  const resultB = await b;
  assert.equal(resultB.token, "new-token");
  assert.equal(srv.refreshes().length, 1);
  assert.equal(srv.posts().length, 1, "only B's request was ever sent");
  assert.equal(srv.refreshes()[0].options.signal, undefined);
  await assertNoUnhandled();
});

test("B: an already-aborted signal sends no request and starts no refresh", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  for (const documentRef of [staleCookie, noCookie]) {
    for (const reason of [undefined, new Error("custom reason")]) {
      const srv = server();
      const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef });
      const controller = new AbortController();
      controller.abort(reason);

      await assert.rejects(client.post(SUBMIT, {}, { signal: controller.signal }), (error) => error.name === "AbortError");
      await assert.rejects(client.get("/api/v1/workspaces/", { signal: controller.signal }), (error) => error.name === "AbortError");

      assert.equal(srv.calls.length, 0);
    }
  }
  await assertNoUnhandled();
});

test("B: a signal aborted while the request is in flight surfaces a plain AbortError, even with a custom reason", async () => {
  const controller = new AbortController();
  const fetchImpl = async () => { controller.abort(new Error("custom reason")); throw new Error("fetch failed after abort"); };
  const client = createApiClient({ fetchImpl, documentRef: staleCookie });
  await assert.rejects(client.post(SUBMIT, {}, { signal: controller.signal }), (error) => error.name === "AbortError");
});

test("B: a failed shared refresh rejects every remaining waiter safely and leaves the aborted one as AbortError", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  const srv = server();
  const gate = srv.holdRefresh();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  const abortA = new AbortController();

  const a = client.post(SUBMIT, { n: "a" }, { signal: abortA.signal });
  const b = client.post(SUBMIT, { n: "b" });
  const c = client.post(SUBMIT, { n: "c" }, { signal: new AbortController().signal });
  const handled = [a, b, c].map((p) => p.then(() => ({ ok: true }), (error) => ({ error })));
  await until(() => srv.posts().length === 3 && srv.state.refreshCount === 1, "three 403s and one refresh");
  await tick();

  abortA.abort();
  gate.reject(new TypeError("network down"));
  const [outA, outB, outC] = await Promise.all(handled);

  assert.equal(outA.error.name, "AbortError");
  assert.equal(outB.error.code, "network_error");
  assert.equal(outC.error.code, "network_error");
  assert.equal(srv.refreshes().length, 1);
  assert.equal(srv.posts().length, 3, "no retries after a failed refresh");

  // The failed refresh did not poison the slot: a later request can refresh and succeed.
  srv.state.gate = null;
  const later = await client.post(SUBMIT, { n: "later" });
  assert.equal(later.result, "ok");
  assert.equal(srv.refreshes().length, 2);
  await assertNoUnhandled();
});

test("B: when every waiter aborts the refresh still completes, the token is cached, and the slot is freed", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  const srv = server();
  const gate = srv.holdRefresh();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  const abortA = new AbortController();
  const abortB = new AbortController();

  const a = client.post(SUBMIT, {}, { signal: abortA.signal });
  const b = client.post(SUBMIT, {}, { signal: abortB.signal });
  const handled = [a, b].map((p) => p.then(() => ({ ok: true }), (error) => ({ error })));
  await until(() => srv.posts().length === 2 && srv.state.refreshCount === 1, "two 403s and one refresh");
  await tick();
  abortA.abort();
  abortB.abort();
  const [outA, outB] = await Promise.all(handled);
  assert.equal(outA.error.name, "AbortError");
  assert.equal(outB.error.name, "AbortError");

  gate.resolve();
  await tick();
  srv.state.gate = null;

  // The refreshed token was cached even though nobody was left to use it: no new refresh needed.
  const next = await client.post(SUBMIT, {});
  assert.equal(next.token, "new-token");
  assert.equal(srv.refreshes().length, 1);

  // The slot was freed: a later CSRF rejection starts a brand-new refresh.
  srv.state.validToken = "newest-token";
  const afterRotation = await client.post(SUBMIT, {});
  assert.equal(afterRotation.token, "newest-token");
  assert.equal(srv.refreshes().length, 2);
  await assertNoUnhandled();
});

test("B: abort listeners are removed once the refresh settles (success, abort and failure)", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);

  function spySignal() {
    const controller = new AbortController();
    const { signal } = controller;
    const active = new Set();
    const add = signal.addEventListener.bind(signal);
    const remove = signal.removeEventListener.bind(signal);
    signal.addEventListener = (type, listener, options) => { if (type === "abort") active.add(listener); return add(type, listener, options); };
    signal.removeEventListener = (type, listener, options) => { if (type === "abort") active.delete(listener); return remove(type, listener, options); };
    return { controller, signal, active };
  }

  // success: listener added while waiting, removed afterwards
  {
    const srv = server();
    const gate = srv.holdRefresh();
    const spy = spySignal();
    const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
    const p = client.post(SUBMIT, {}, { signal: spy.signal });
    await until(() => srv.state.refreshCount === 1, "refresh");
    await tick();
    assert.equal(spy.active.size, 1, "one listener while waiting");
    gate.resolve();
    assert.equal((await p).result, "ok");
    assert.equal(spy.active.size, 0);
    spy.controller.abort(); // late abort of a finished request must be a harmless no-op
    await tick();
  }

  // abort: listener gone once the shared refresh later settles
  {
    const srv = server();
    const gate = srv.holdRefresh();
    const spy = spySignal();
    const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
    const p = client.post(SUBMIT, {}, { signal: spy.signal });
    const handled = p.catch((error) => error);
    await until(() => srv.state.refreshCount === 1, "refresh");
    await tick();
    spy.controller.abort();
    assert.equal((await handled).name, "AbortError");
    gate.resolve();
    await tick();
    assert.equal(spy.active.size, 0);
  }

  // failure: listener removed when the refresh rejects
  {
    const srv = server();
    const gate = srv.holdRefresh();
    const spy = spySignal();
    const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
    const p = client.post(SUBMIT, {}, { signal: spy.signal });
    const handled = p.catch((error) => error);
    await until(() => srv.state.refreshCount === 1, "refresh");
    await tick();
    gate.reject(new TypeError("network down"));
    assert.equal((await handled).code, "network_error");
    assert.equal(spy.active.size, 0);
  }
  await assertNoUnhandled();
});

test("B: a request without a signal behaves exactly as before while others abort", async (t) => {
  const assertNoUnhandled = trackUnhandled(t);
  const srv = server();
  const client = createApiClient({ fetchImpl: srv.fetchImpl, documentRef: staleCookie });
  const results = await Promise.all([client.post(SUBMIT, {}), client.post(SUBMIT, {}), client.post(SUBMIT, {})]);
  assert.deepEqual(results.map((r) => r.result), ["ok", "ok", "ok"]);
  assert.equal(srv.refreshes().length, 1, "concurrent failures still share one refresh");
  await assertNoUnhandled();
});
