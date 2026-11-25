import { getJson } from "./apiClient.js";

const HEALTH_ENDPOINT = "/api/v1/health/";

export async function getBackendHealth({ signal } = {}) {
  const payload = await getJson(HEALTH_ENDPOINT, { signal });
  if (
    payload?.status !== "ok" ||
    payload?.service !== "codewise-backend" ||
    payload?.api_version !== "v1"
  ) {
    throw new Error("The API health response did not match the expected contract.");
  }
  return payload;
}