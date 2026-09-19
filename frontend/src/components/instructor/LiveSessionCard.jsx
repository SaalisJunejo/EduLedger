import { useEffect, useRef, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
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
 * 2 seconds, shows a countdown to the next nonce rotation, and lets the
 * instructor end the view.
 */
export default function LiveSessionCard({ session, onEnded }) {
  const [qr, setQr] = useState(null);
  const [qrError, setQrError] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const clockOffsetRef = useRef(0); // server_time - client clock, for the countdown
  const stoppedRef = useRef(false);
  const onEndedRef = useRef(onEnded);

  useEffect(() => {
    onEndedRef.current = onEnded;
  }, [onEnded]);

  useEffect(() => {
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
  }, [session.id]);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const rotation = qr ? qr.rotation_seconds : session.rotation_seconds || 5;
  const secondsLeft = qr
    ? Math.max(0, (Date.parse(qr.nonce_expires_at) - (now + clockOffsetRef.current)) / 1000)
    : rotation;
  const percent = Math.min(100, (secondsLeft / rotation) * 100);

  function endSession() {
    stoppedRef.current = true;
    onEndedRef.current("ended", null);
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-1 text-xs font-bold tracking-wide text-success">
            <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
            LIVE
          </span>
          <h2 className="text-base font-bold text-ink">{session.class_id}</h2>
        </div>
        <p className="text-xs text-ink-muted">
          Session #{session.id} · started {formatTime(session.start_time)}
        </p>
      </div>

      {qrError ? (
        <div className="mt-4">
          <StatusBanner tone="warning" title="Lost contact with the QR feed">
            {describeApiError(qrError)} — retrying automatically.
          </StatusBanner>
        </div>
      ) : null}

      {qr ? (
        <div className="mt-5">
          <div className="rounded-xl border border-line bg-white p-4">
            <img
              src={qr.qr_image}
              alt="Live attendance QR code"
              className="mx-auto w-full max-w-[420px] [image-rendering:pixelated]"
            />
          </div>
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs font-medium text-ink-muted">
              <span>Rotating nonce — new code every {rotation}s</span>
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
        </div>
      ) : (
        <div className="mt-6 flex flex-col items-center gap-2 py-8 text-ink-muted">
          <Spinner className="h-6 w-6" />
          <p className="text-sm">Loading the live QR code…</p>
        </div>
      )}

      <p className="mt-4 rounded-lg bg-surface-muted px-3 py-2.5 text-xs leading-relaxed text-ink-muted">
        Live attendance count: not available yet — the backend exposes no count
        endpoint for a session (needed for this projector view).
      </p>

      <div className="mt-5 flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs leading-relaxed text-ink-muted sm:max-w-md">
          Ending stops this projector view. The backend has no end-session
          endpoint yet — the server closes a session when a new one starts for
          the same class.
        </p>
        <Button variant="danger" onClick={endSession}>
          End Session
        </Button>
      </div>
    </Card>
  );
}
