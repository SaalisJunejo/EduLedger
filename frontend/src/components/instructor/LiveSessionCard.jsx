import { useCallback, useEffect, useRef, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import LiveAttendancePanel from "./LiveAttendancePanel.jsx";
import { api } from "../../lib/api.js";
import { describeApiError } from "../../lib/attendanceMessages.js";

const QR_POLL_MS = 2000;
const CLOCK_TICK_MS = 250;

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

/**
 * Projector view for one live session: polls the rotating QR endpoint every
 * 2 seconds, shows the 6-digit numeric code derived from the same nonce, a
 * countdown to the next rotation and the live attendance roster beside them,
 * and lets the conductor end the session for good.
 */
export default function LiveSessionCard({ session, onEnded }) {
  const [qr, setQr] = useState(null);
  const [qrError, setQrError] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const [ended, setEnded] = useState(null); // { count } once closed server-side
  const [ending, setEnding] = useState(false);
  const [endError, setEndError] = useState(null);
  const clockOffsetRef = useRef(0); // server_time - client clock, for the countdown
  const stoppedRef = useRef(false);
  const endingRef = useRef(false); // an /end call is in flight
  const onEndedRef = useRef(onEnded);

  useEffect(() => {
    onEndedRef.current = onEnded;
  }, [onEnded]);

  useEffect(() => {
    if (ended) return undefined; // closed server-side: nothing left to rotate
    let inFlight = false;
    stoppedRef.current = false;

    async function poll() {
      if (stoppedRef.current || inFlight) return;
      inFlight = true;
      try {
        const data = await api(`/session/${session.id}/qr`);
        if (stoppedRef.current) return;
        clockOffsetRef.current = Date.parse(data.server_time) - Date.now();
        setQr(data);
        setQrError(null);
      } catch (error) {
        if (stoppedRef.current) return;
        if (error.status === 404 || error.status === 409) {
          if (endingRef.current) return; // our own /end just landed
          // The backend closed/replaced this session — stop polling for good.
          stoppedRef.current = true;
          onEndedRef.current("closed", error);
          return;
        }
        setQrError(error); // transient hiccup (e.g. backend restart) — keep polling
      } finally {
        inFlight = false;
      }
    }

    poll();
    const timer = setInterval(poll, QR_POLL_MS);
    return () => {
      stoppedRef.current = true;
      clearInterval(timer);
    };
  }, [session.id, ended]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const rotation = qr ? qr.rotation_seconds : session.rotation_seconds || 10;
  const secondsLeft = qr
    ? Math.max(0, (Date.parse(qr.nonce_expires_at) - (now + clockOffsetRef.current)) / 1000)
    : rotation;
  const percent = Math.min(100, (secondsLeft / rotation) * 100);

  // The server also broadcasts "session_ended" to the room, so a session
  // ended from another device (or by a substitute) stops this view too.
  const handleSessionEnded = useCallback((count) => {
    stoppedRef.current = true;
    setEnded({ count: count ?? null });
  }, []);

  async function endSession() {
    if (ending || ended) return;
    const confirmed = window.confirm(
      "End this session? The QR and the numeric code stop rotating and no student can check in afterwards."
    );
    if (!confirmed) return;

    setEnding(true);
    setEndError(null);
    // Set before the call: the session is inactive the moment the backend
    // commits, so a QR poll crossing it must not report "closed" first.
    endingRef.current = true;
    try {
      const data = await api(`/session/${session.id}/end`, { method: "POST" });
      stoppedRef.current = true;
      setEnded({ count: data.count ?? null });
    } catch (error) {
      endingRef.current = false;
      if (error.status === 404) {
        // Already gone from the backend — hand it to the parent as closed.
        stoppedRef.current = true;
        onEndedRef.current("closed", error);
        return;
      }
      setEndError(error);
    } finally {
      setEnding(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          {ended ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-muted px-2.5 py-1 text-xs font-bold tracking-wide text-ink-muted">
              ENDED
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-1 text-xs font-bold tracking-wide text-success">
              <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
              LIVE
            </span>
          )}
          <h2 className="text-base font-bold text-ink">{session.class_id}</h2>
        </div>
        <p className="text-xs text-ink-muted">
          Session #{session.id} · started {formatTime(session.start_time)}
        </p>
      </div>

      {qrError && !ended ? (
        <div className="mt-4">
          <StatusBanner tone="warning" title="Lost contact with the QR feed">
            {describeApiError(qrError)} — retrying automatically.
          </StatusBanner>
        </div>
      ) : null}

      {ended ? (
        <div className="mt-4">
          <StatusBanner tone="info" title="Session ended">
            No more check-ins — the QR and the numeric code stopped rotating.
            {ended.count != null ? (
              <>
                {" "}Final attendance:{" "}
                <span className="font-semibold">
                  {ended.count} {ended.count === 1 ? "student" : "students"}
                </span>
                .
              </>
            ) : null}
          </StatusBanner>
        </div>
      ) : null}

      <div className="mt-5 grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          {ended ? (
            <p className="rounded-xl border border-line bg-surface-muted px-4 py-10 text-center text-sm leading-relaxed text-ink-muted">
              The projected code is gone. Start a new session to take attendance
              for this class again.
            </p>
          ) : qr ? (
            <>
              {/* Fixed-size square QR (never a share of the column, which once
                  collapsed it to 0px beside the code); the code wraps below
                  it whenever the two don't fit on one line. */}
              <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-4 rounded-xl border border-line bg-white p-4">
                <img
                  src={qr.qr_image}
                  alt="Live attendance QR code"
                  className="aspect-square w-[min(60vh,420px,100%)] shrink-0 [image-rendering:pixelated]"
                />
                {qr.numeric_code ? (
                  <div className="shrink-0 text-center">
                    <p className="text-xs font-bold uppercase tracking-wide text-ink-muted">
                      Or enter code
                    </p>
                    <p
                      className="mt-1 font-mono text-5xl font-bold tracking-widest text-ink tabular-nums sm:text-6xl"
                      aria-live="polite"
                    >
                      {qr.numeric_code.slice(0, 3)} {qr.numeric_code.slice(3)}
                    </p>
                  </div>
                ) : null}
              </div>
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs font-medium text-ink-muted">
                  <span>Rotating nonce and code — new every {rotation}s</span>
                  <span className={secondsLeft < 1.5 ? "font-bold text-warning" : ""}>
                    next code in {secondsLeft.toFixed(1)}s
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-[width] duration-200 ease-linear"
                    style={{ width: `${percent}%` }}
                  />
                </div>
              </div>
              <p className="mt-3 text-center font-mono text-xs text-ink-muted">
                nonce {qr.current_nonce}
              </p>
            </>
          ) : (
            <div className="flex flex-col items-center gap-2 py-8 text-ink-muted">
              <Spinner className="h-6 w-6" />
              <p className="text-sm">Loading the live QR code…</p>
            </div>
          )}
        </div>

        <LiveAttendancePanel sessionId={session.id} onSessionEnded={handleSessionEnded} />
      </div>

      {endError ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Couldn't end the session">
            {describeApiError(endError)}
          </StatusBanner>
        </div>
      ) : null}

      <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
        {ended ? (
          <>
            <p className="text-xs leading-relaxed text-ink-muted sm:max-w-md">
              The session is closed on the backend, so later scans are rejected.
              Attendance already locked on-chain stays untouched.
            </p>
            <Button variant="secondary" onClick={() => onEndedRef.current("ended", null)}>
              Back to classes
            </Button>
          </>
        ) : (
          <>
            <p className="text-xs leading-relaxed text-ink-muted sm:max-w-md">
              Ending closes the session on the backend: rotation stops, later
              scans are rejected and everyone watching this session is notified.
            </p>
            <Button variant="danger" onClick={endSession} loading={ending}>
              End Session
            </Button>
          </>
        )}
      </div>
    </Card>
  );
}
