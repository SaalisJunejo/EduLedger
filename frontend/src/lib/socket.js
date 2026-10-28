/**
 * Socket.IO client for the live attendance panel (Phase 1.2b).
 *
 * Same-origin on the default /socket.io path (Vite proxies it to Flask in
 * dev — see vite.config.js). The backend authenticates the handshake itself
 * (decision D5), so the stored JWT is handed over as `auth: { token }` and
 * refreshed before every attempt.
 *
 * One connection per watched session, so every socket sits in exactly one
 * "session:<id>" room: the server's "student_checked_in" payload carries no
 * session id, and the instructor dashboard can hold two live sessions at
 * once (the attendance tab and the workload tab both stay mounted). A
 * connection per room keeps those two rosters from bleeding into each other.
 */
import { io } from "socket.io-client";
import { getToken } from "./auth.js";

const RETRY_MS = 3000;

// session id -> { socket, retryTimer }
const connections = new Map();

function key(sessionId) {
  return String(sessionId);
}

/**
 * The socket watching one session, created and connected on first use.
 * Returns null when nobody is logged in (no token to hand the handshake).
 */
export function connectSession(sessionId) {
  const token = getToken();
  if (!token) return null;

  const existing = connections.get(key(sessionId));
  if (existing) {
    // Always hand the handshake the token that is stored *now*.
    existing.socket.auth = { token };
    if (!existing.socket.connected) existing.socket.connect();
    return existing.socket;
  }

  const socket = io({ path: "/socket.io", auth: { token } });
  const entry = { socket, retryTimer: null };
  connections.set(key(sessionId), entry);

  // A refused handshake (missing or expired token) is a namespace-level
  // error, which socket.io never retries on its own — so re-arm it here.
  // Transport hiccups keep `active` true and retry themselves.
  socket.on("connect_error", () => {
    if (socket.active || entry.retryTimer) return;
    entry.retryTimer = setTimeout(() => {
      entry.retryTimer = null;
      const fresh = getToken();
      if (!fresh || !connections.has(key(sessionId))) return; // signed out
      socket.auth = { token: fresh };
      socket.connect();
    }, RETRY_MS);
  });

  return socket;
}

/** Subscribe the session's socket to its live roster room. */
export function joinSession(sessionId) {
  const socket = connectSession(sessionId);
  if (!socket) return null;
  socket.emit("join_session", { session_id: sessionId });
  return socket;
}

/** Close the socket watching one session (the panel unmounting). */
export function leaveSession(sessionId) {
  const entry = connections.get(key(sessionId));
  if (!entry) return;
  connections.delete(key(sessionId));
  if (entry.retryTimer) clearTimeout(entry.retryTimer);
  entry.socket.removeAllListeners();
  entry.socket.disconnect();
}

/** Close every live socket for good (logout). */
export function disconnectSocket() {
  for (const sessionId of [...connections.keys()]) leaveSession(sessionId);
}
