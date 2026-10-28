import { useCallback, useEffect, useRef, useState } from "react";
import StatusBanner from "../ui/StatusBanner.jsx";
import { api } from "../../lib/api.js";
import { describeApiError } from "../../lib/attendanceMessages.js";
import { connectSession, joinSession, leaveSession } from "../../lib/socket.js";

const FLASH_MS = 2500;

// Check-ins happen during the class, so only the clock matters — in the
// timetable's zone, never the browser's (a projector abroad still shows
// Karachi time).
function formatCheckInTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return date.toLocaleTimeString("en-US", {
      timeZone: "Asia/Karachi",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function checkedInAt(row) {
  const parsed = Date.parse(row.checked_in_at);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/**
 * Newest first, one row per student. The REST roster and the live events
 * are merged (never replaced) so an event that lands while /attendees is
 * in flight can't be dropped — and a replayed one can't duplicate a row.
 */
function mergeRows(previous, incoming) {
  const byStudent = new Map();
  for (const row of previous) byStudent.set(String(row.student_id), row);
  for (const row of incoming) {
    if (row && row.student_id != null && !byStudent.has(String(row.student_id))) {
      byStudent.set(String(row.student_id), row);
    }
  }
  return [...byStudent.values()].sort(
    (left, right) => checkedInAt(right) - checkedInAt(left)
  );
}

/**
 * Live attendance roster for one session (Phase 1.2b).
 *
 * The socket joins "session:<id>" and the server pushes one
 * "student_checked_in" per on-chain-locked scan. GET /attendees is loaded
 * on join and after every reconnect, so a check-in that happened while the
 * transport was down still appears; events and roster are deduplicated by
 * student_id. "session_ended" is handed to the parent, which stops the QR.
 */
export default function LiveAttendancePanel({ sessionId, onSessionEnded }) {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("connecting"); // connecting|live|reconnecting|refused
  const [joinError, setJoinError] = useState(null); // { check, error, message }
  const [loadError, setLoadError] = useState(null); // ApiError from /attendees
  const [flashed, setFlashed] = useState(() => new Set());
  const mountedRef = useRef(true);
  const onSessionEndedRef = useRef(onSessionEnded);

  useEffect(() => {
    onSessionEndedRef.current = onSessionEnded;
  }, [onSessionEnded]);

  const loadAttendees = useCallback(async () => {
    try {
      const data = await api(`/session/${sessionId}/attendees`);
      if (!mountedRef.current) return;
      setRows((previous) => mergeRows(previous, data.attendees || []));
      setLoadError(null);
    } catch (error) {
      if (mountedRef.current) setLoadError(error);
    }
  }, [sessionId]);

  useEffect(() => {
    mountedRef.current = true;
    const flashTimers = [];
    const socket = connectSession(sessionId);

    if (!socket) {
      setStatus("refused");
      setJoinError({
        message: "You're signed out — sign in again to watch check-ins live.",
      });
      loadAttendees();
      return () => {
        mountedRef.current = false;
      };
    }

    function join() {
      setStatus("connecting");
      joinSession(sessionId);
    }

    function handleJoined(data) {
      if (data && String(data.session_id) !== String(sessionId)) return;
      setStatus("live");
      setJoinError(null);
      // Catch up on everything missed before/while joining, then let the
      // live events extend the roster.
      loadAttendees();
    }

    function handleJoinError(payload) {
      setStatus("refused");
      setJoinError(payload || null);
    }

    function handleCheckedIn(payload) {
      if (!payload || payload.student_id == null) return;
      setRows((previous) => mergeRows(previous, [payload]));
      const id = String(payload.student_id);
      setFlashed((previous) => new Set(previous).add(id));
      flashTimers.push(
        setTimeout(() => {
          if (!mountedRef.current) return;
          setFlashed((previous) => {
            const next = new Set(previous);
            next.delete(id);
            return next;
          });
        }, FLASH_MS)
      );
    }

    function handleSessionEnded(payload) {
      if (payload && String(payload.session_id) !== String(sessionId)) return;
      loadAttendees(); // final roster, straight from the backend
      onSessionEndedRef.current?.(payload ? payload.count : null);
    }

    socket.on("connect", join);
    socket.on("disconnect", () => setStatus("reconnecting"));
    socket.on("connect_error", () => setStatus("reconnecting"));
    socket.on("joined", handleJoined);
    socket.on("join_error", handleJoinError);
    socket.on("student_checked_in", handleCheckedIn);
    socket.on("session_ended", handleSessionEnded);

    // Already connected (a remount on the same session): "connect" won't
    // fire again, so join right away.
    if (socket.connected) join();
    // The roster renders even when the socket never comes up.
    loadAttendees();

    return () => {
      mountedRef.current = false;
      flashTimers.forEach(clearTimeout);
      leaveSession(sessionId);
    };
  }, [sessionId, loadAttendees]);

  const count = rows.length;

  return (
    <div className="flex h-full flex-col rounded-xl border border-line bg-surface-muted p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-ink-muted">
            Live attendance
          </p>
          <p className="mt-1 text-4xl font-bold leading-none text-ink tabular-nums" aria-live="polite">
            {count}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            {count === 1 ? "student checked in" : "students checked in"}
          </p>
        </div>
        <ConnectionBadge status={status} />
      </div>

      {status === "refused" ? (
        <div className="mt-3">
          <StatusBanner
            tone="warning"
            title="Live updates unavailable"
            detail={joinError?.message || null}
          >
            {describeJoinError(joinError)}
          </StatusBanner>
        </div>
      ) : null}

      {loadError ? (
        <div className="mt-3">
          <StatusBanner tone="warning" title="Couldn't load the attendee list">
            {describeApiError(loadError)}
          </StatusBanner>
        </div>
      ) : null}

      <ul className="mt-4 flex max-h-80 flex-col gap-2 overflow-y-auto">
        {rows.map((row) => (
          <li
            key={row.student_id}
            className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 transition-colors duration-700 ${
              flashed.has(String(row.student_id))
                ? "border-success/30 bg-success-soft"
                : "border-line bg-surface"
            }`}
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-ink">{row.full_name}</p>
              <p className="truncate font-mono text-xs text-ink-muted">
                {row.roll_number || "No roll number"}
              </p>
            </div>
            <p className="shrink-0 text-xs font-medium text-ink-muted tabular-nums">
              {formatCheckInTime(row.checked_in_at)}
            </p>
          </li>
        ))}
      </ul>

      {count === 0 ? (
        <p className="mt-4 rounded-lg bg-surface px-3 py-3 text-xs leading-relaxed text-ink-muted">
          Nobody has checked in yet. Each student appears here the moment their
          attendance is locked on-chain.
        </p>
      ) : null}

      <p className="mt-auto pt-3 text-[11px] leading-relaxed text-ink-muted">
        Times are Asia/Karachi. Newest check-in first.
      </p>
    </div>
  );
}

function ConnectionBadge({ status }) {
  if (status === "live") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-1 text-xs font-bold text-success">
        <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
        Live
      </span>
    );
  }
  if (status === "refused") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-danger-soft px-2.5 py-1 text-xs font-bold text-danger-strong">
        Offline
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-warning-soft px-2.5 py-1 text-xs font-bold text-warning">
      <span className="h-2 w-2 animate-pulse rounded-full bg-warning" />
      Reconnecting
    </span>
  );
}

// The socket replies with the REST error shape, so the same
// "<check>/<error>" wording table applies — minus the ApiError wrapper.
function describeJoinError(payload) {
  if (!payload) return "The live feed couldn't be joined.";
  return describeApiError({
    check: payload.check || null,
    code: payload.error || null,
    message: payload.message || null,
  });
}
