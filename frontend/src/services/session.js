import { apiGet, apiPost } from "./apiClient.js";

export function createSessionService(client = { get: apiGet, post: apiPost }) {
  return {
    getSession() { return client.get("/api/v1/session/"); },
    getWorkspaces() { return client.get("/api/v1/workspaces/"); },
    login({ username, password }) {
      if (!String(username ?? "").trim() || typeof password !== "string" || !password) {
        throw new TypeError("Enter your username and password.");
      }
      return client.post("/api/v1/session/login/", { username: username.trim(), password });
    },
    logout() { return client.post("/api/v1/session/logout/", {}); },
  };
}

const sessionService = createSessionService();
export const getSession = () => sessionService.getSession();
export const getWorkspaces = () => sessionService.getWorkspaces();
export const login = (credentials) => sessionService.login(credentials);
export const logout = () => sessionService.logout();
