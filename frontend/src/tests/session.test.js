import test from "node:test";
import assert from "node:assert/strict";
import { createSessionService } from "../services/session.js";

test("session service uses authenticated session and workspace discovery endpoints", async () => {
  const calls = [];
  const client = {
    get: async (path) => { calls.push(["GET", path]); return path.endsWith("/session/")
      ? { is_authenticated: true, username: "ada" } : { workspaces: [{ id: "workspace-id", role: "owner" }] }; },
    post: async (path, body) => { calls.push(["POST", path, body]); return { is_authenticated: true }; },
  };
  const service = createSessionService(client);
  assert.equal((await service.getSession()).username, "ada");
  assert.equal((await service.getWorkspaces()).workspaces[0].role, "owner");
  await service.login({ username: " ada ", password: "secret" });
  await service.logout();
  assert.deepEqual(calls, [
    ["GET", "/api/v1/session/"], ["GET", "/api/v1/workspaces/"],
    ["POST", "/api/v1/session/login/", { username: "ada", password: "secret" }],
    ["POST", "/api/v1/session/logout/", {}],
  ]);
});

test("session service validates credentials before posting", () => {
  const service = createSessionService({ get: async () => ({}), post: async () => ({}) });
  assert.throws(() => service.login({ username: "", password: "x" }), /username and password/);
  assert.throws(() => service.login({ username: "ada", password: "" }), /username and password/);
});
