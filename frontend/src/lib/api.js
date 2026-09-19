/**
 * Tiny fetch wrapper for the EduLedger Flask API. In dev, Vite proxies
 * /api/* to the backend (see vite.config.js) so calls are same-origin.
 *
 * Non-2xx responses throw an ApiError carrying the backend's structured
 * error body: { error: <code>, check: <check name>, message, ...details }.
 */
const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, payload) {
    const message = (payload && (payload.message || payload.error)) || `Request failed (HTTP ${status})`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = (payload && payload.error) || null;
    this.check = (payload && payload.check) || null;
    this.details = payload || {};
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${BASE}${path}`, options);
  } catch {
    throw new ApiError(0, {
      error: "network_error",
      message: "Cannot reach the backend — is Flask running on port 5000?",
    });
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body (e.g. an empty response) */
  }

  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload;
}
