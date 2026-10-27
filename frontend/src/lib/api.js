/**
 * Tiny fetch wrapper for the EduLedger Flask API. In dev, Vite proxies
 * /api/* to the backend (see vite.config.js) so calls are same-origin.
 *
 * Non-2xx responses throw an ApiError carrying the backend's structured
 * error body: { error: <code>, check: <check name>, message, ...details }.
 */
import { getToken, logout } from "./auth.js";

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
  const token = getToken();
  if (token) options.headers.Authorization = `Bearer ${token}`;
  if (body instanceof FormData) {
    // The browser supplies the multipart Content-Type and boundary.
    options.body = body;
  } else if (body !== undefined) {
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
    if (response.status === 401) {
      logout();
      // Keep login failures on the form so its backend message remains visible.
      if (window.location.pathname !== "/login") window.location.replace("/login");
    }
    throw new ApiError(response.status, payload);
  }
  return payload;
}
