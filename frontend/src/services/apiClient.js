const DEFAULT_API_BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? "";
const CSRF_ENDPOINT = "/api/v1/session/csrf/";

const ERROR_MESSAGES = {
  authentication_required: "Your Django session is missing or expired. Sign in through your configured session provider, then retry.",
  workspace_access_denied: "Your account does not have access to this workspace or action.",
  invalid_repository_url: "Enter a public GitHub repository URL such as https://github.com/owner/repository.",
  repository_not_public: "Only public GitHub repositories are supported by this connection.",
  idempotency_key_conflict: "This request key was already used for different options. Start the request again.",
  workflow_runtime_unavailable: "The workflow runtime is not configured on this backend yet.",
  durable_approval_checkpoint_unavailable: "This backend does not have durable approval checkpoints configured, so it cannot safely pause for approval.",
  approval_review_not_ready: "Agent 8 does not return an eligible review. The backend rejected this decision.",
  approval_not_pending: "This execution no longer has a pending human decision.",
  approval_agents_not_selected: "Select both Patch Engineering and Final Review to request human approval.",
  submission_not_ready: "The repository snapshot is not sealed yet. Check the submission status and retry.",
  snapshot_access_unavailable: "The snapshot is no longer available or its repository access grant has expired.",
  csrf_failed: "The browser security token expired. Refresh the page and try again.",
  forbidden: "The request was blocked before it reached the CodeWise API. Check your network or proxy and try again.",
  internal_error: "The backend could not complete this request. Try again later.",
};

export class ApiError extends Error {
  constructor({ status = 0, code = "network_error", message, details = null }) {
    super(message ?? ERROR_MESSAGES[code] ?? friendlyCode(code));
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function friendlyCode(code) {
  return String(code || "request_failed").replaceAll("_", " ");
}

function readCookie(name, documentRef = globalThis.document) {
  if (!documentRef?.cookie) return "";
  const prefix = `${encodeURIComponent(name)}=`;
  const part = documentRef.cookie.split(";").map(item => item.trim()).find(item => item.startsWith(prefix));
  return part ? decodeURIComponent(part.slice(prefix.length)) : "";
}

function createRequestKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  globalThis.crypto?.getRandomValues?.(bytes);
  if (!bytes.some(Boolean)) return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return [...bytes].map(v => v.toString(16).padStart(2, "0")).join("");
}

function validatePath(path) {
  if (typeof path !== "string" || !path.startsWith("/api/v1/") || path.startsWith("//") || path.includes("\\")) {
    throw new TypeError("API paths must be relative to /api/v1/.");
  }
  return path;
}

async function parseResponse(response) {
  if (response.status === 204) return null;
  const contentType = response.headers?.get?.("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    if (!response.ok) throw new ApiError({
      status: response.status,
      // A non-JSON 403 is not a CSRF signal: Django's CSRF_FAILURE_VIEW (api/csrf.py) answers with
      // JSON {"error": "csrf_failed"}, so an HTML 403 comes from something else (proxy, WAF, CDN).
      code: response.status === 401 ? "authentication_required" : response.status === 403 ? "forbidden" : "invalid_response"
    });
    throw new ApiError({
      status: response.status,
      code: "invalid_response",
      message: "The backend returned an unexpected response. Check that the API and frontend share the configured origin."
    });
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError({ status: response.status, code: "invalid_response", message: "The backend returned invalid JSON." });
  }
}

function responseError(response, payload) {
  let code = payload?.error;
  if (!code && (response.status === 401 || response.status === 403)) {
    const detail = String(payload?.detail ?? "").toLowerCase();
    code = detail.includes("csrf") ? "csrf_failed" : "authentication_required";
  }
  code ||= response.status >= 500 ? "internal_error" : "request_failed";
  return new ApiError({ status: response.status, code, details: payload });
}

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError();
}

export function createApiClient({ baseUrl = DEFAULT_API_BASE_URL, fetchImpl = globalThis.fetch, documentRef = globalThis.document } = {}) {
  const origin = String(baseUrl).replace(/\\+$/, "");
  let csrfTokenCache = "";
  let csrfTokenVersion = 0; // bumped whenever the server hands us a token
  let csrfRefreshPromise = null; // shared so concurrent callers trigger one refresh

  function storeCsrfToken(token) {
    csrfTokenCache = token;
    csrfTokenVersion += 1;
  }

  // Fetch a CSRF token without triggering the retry loop. No caller AbortSignal is used
  // here: this request is shared, so one caller aborting must not fail the others.
  async function fetchCsrfToken() {
    let resp;
    try {
      resp = await fetchImpl(`${origin}${CSRF_ENDPOINT}`, {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        headers: {},
      });
    } catch {
      throw new ApiError({ code: "network_error", message: "Could not reach the CodeWise API. Check your connection and retry." });
    }
    const payload = await parseResponse(resp);
    if (!resp.ok) {
      throw responseError(resp, payload);
    }
    if (typeof payload?.csrfToken === "string") {
      storeCsrfToken(payload.csrfToken);
    }
    return payload;
  }

  // Single shared refresh. The slot is cleared when this exact promise settles, on
  // success or failure, so a failed refresh never poisons later requests.
  function refreshCsrfToken() {
    if (!csrfRefreshPromise) {
      const refresh = fetchCsrfToken().finally(() => {
        if (csrfRefreshPromise === refresh) csrfRefreshPromise = null;
      });
      csrfRefreshPromise = refresh;
    }
    return csrfRefreshPromise;
  }

  // Wait for the shared refresh on behalf of one caller. Aborting the caller's signal rejects
  // only this caller, immediately; the shared refresh itself is never cancelled, so other
  // waiters (and the token cache) are unaffected. The listener is always removed.
  function waitForRefresh(signal) {
    throwIfAborted(signal); // never start or join a refresh for an already-aborted caller
    const shared = refreshCsrfToken();
    if (!signal) return shared;
    return new Promise((resolve, reject) => {
      const onAbort = () => reject(abortError());
      signal.addEventListener("abort", onAbort, { once: true });
      // Both handlers are supplied, so `shared` is always handled even if this caller already left.
      shared.then(
        (value) => { signal.removeEventListener("abort", onAbort); resolve(value); },
        (error) => { signal.removeEventListener("abort", onAbort); reject(error); }
      );
    });
  }

  // Core request sender with CSRF handling and a single retry
  async function send(path, { method = "GET", body, idempotencyKey, signal } = {}) {
    validatePath(path);
    throwIfAborted(signal);
    const normalizedMethod = method.toUpperCase();
    const changesState = ["POST", "PUT", "PATCH", "DELETE"].includes(normalizedMethod);
    // Generated once per original request so a retry reuses exactly the same key.
    const requestKey = normalizedMethod === "POST" && path !== CSRF_ENDPOINT
      ? (idempotencyKey || createRequestKey())
      : undefined;
    let hasRetried = false;
    while (true) {
      throwIfAborted(signal);
      // Build fresh headers for each attempt
      const headers = { Accept: "application/json" };
      const options = { method: normalizedMethod, headers, credentials: "include", cache: "no-store", signal };
      let tokenVersion = csrfTokenVersion;

      if (body !== undefined) {
        if (!body || typeof body !== "object" || Array.isArray(body)) {
          throw new TypeError("JSON request bodies must be objects.");
        }
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
      }

      // Attach CSRF token and Idempotency-Key when needed
      if (changesState) {
        // A token issued by the server (bootstrap, refresh, login rotation) always wins over a
        // possibly stale readable cookie. The cookie is only used before any token was issued.
        let token = csrfTokenCache || readCookie("csrftoken", documentRef);

        // Bootstrap a token if one is still unavailable (and this is not the CSRF endpoint itself)
        if (!token && path !== CSRF_ENDPOINT) {
          await waitForRefresh(signal);
          token = csrfTokenCache || readCookie("csrftoken", documentRef);
        }

        // If still missing token for a state-changing request, abort
        if (!token && path !== CSRF_ENDPOINT) {
          throw new ApiError({
            status: 403,
            code: "csrf_token_unavailable",
            message: "The browser security token is unavailable. Refresh the page and try again."
          });
        }

        tokenVersion = csrfTokenVersion;
        if (token) {
          headers["X-CSRFToken"] = token;
        }

        if (requestKey) {
          headers["Idempotency-Key"] = requestKey;
        }
      }

      throwIfAborted(signal); // last check before anything is sent
      let response;
      try {
        response = await fetchImpl(`${origin}${path}`, options);
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throwIfAborted(signal); // aborted with a custom reason: still surface a plain AbortError
        throw new ApiError({ code: "network_error", message: "Could not reach the CodeWise API. Check your connection and retry." });
      }
      const payload = await parseResponse(response);
      if (response.ok) {
        if (typeof payload?.csrfToken === "string") storeCsrfToken(payload.csrfToken);
        return payload;
      }
      const err = responseError(response, payload);
      if (!hasRetried && err.code === "csrf_failed") {
        hasRetried = true;
        // If another request already refreshed the token since this one was built, just retry
        // with it; otherwise join (or start) the shared refresh.
        if (csrfTokenVersion === tokenVersion) {
          await waitForRefresh(signal);
        }
        continue; // retry once with the refreshed token
      }
      throw err;
    }
  }

  return {
    get(path, options = {}) { return send(path, { ...options, method: "GET" }); },
    post(path, body, options = {}) { return send(path, { ...options, method: "POST", body }); },
    request: send,
  };
}

const apiClient = createApiClient();
export const apiGet = (path, options) => apiClient.get(path, options);
export const apiPost = (path, body, options) => apiClient.post(path, body, options);
export const getJson = apiGet;
export { createRequestKey };
