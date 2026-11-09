export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').trim().replace(/\/+$/, '');

export class ApiError extends Error {
  constructor(status, payload = {}) {
    super(payload.message || `Request failed (${status}).`);
    this.name = 'ApiError';
    this.status = status;
    this.check = payload.check ?? 'request';
    this.error = payload.error ?? 'request_failed';
  }
}

// Auth owns persistence; injected callbacks avoid a circular api/auth import.
let getToken = () => null;
let onUnauthorized = async (_error, _requestToken) => {};

export function configureAuth(handlers) {
  getToken = handlers.getToken;
  onUnauthorized = handlers.onUnauthorized;
}

/** Resolve JSON on success; reject with an ApiError preserving backend fields. */
export async function api(path, { method = 'GET', body, auth = true, headers = {} } = {}) {
  if (!API_URL) {
    throw new ApiError(0, {
      check: 'configuration',
      error: 'missing_api_url',
      message: 'Set EXPO_PUBLIC_API_URL in mobile/.env and restart Expo.',
    });
  }

  const requestToken = auth ? getToken() : null;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  let response;
  let payload;
  try {
    response = await fetch(`${API_URL}/${path.replace(/^\/+/, '')}`, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
        ...(requestToken ? { Authorization: `Bearer ${requestToken}` } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
    });
    const text = await response.text();
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = null;
    }
  } catch (error) {
    throw new ApiError(0, {
      check: 'network',
      error: error.name === 'AbortError' ? 'request_timeout' : 'network_error',
      message: error.name === 'AbortError'
        ? 'The request timed out. Please try again.'
        : 'Cannot reach the server. Check the API URL, Wi-Fi, and PC firewall.',
    });
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    const error = new ApiError(response.status, payload || {
      message: `The server returned HTTP ${response.status}. Please try again.`,
    });
    if (response.status === 401 && (payload?.check ?? 'auth') === 'auth') {
      // Only the JWT session layer signs the student out. The device-identity
      // and verification-token layers also answer 401, and those must reach
      // the attendance screen as retryable failures instead of a logout.
      // A failed login (check "auth") simply clears an already-empty session.
      await onUnauthorized(error, requestToken);
    }
    throw error;
  }
  if (payload === null) {
    throw new ApiError(response.status, {
      check: 'response', error: 'invalid_json', message: 'The server returned an invalid response.',
    });
  }
  return payload;
}
