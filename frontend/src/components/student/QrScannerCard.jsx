import { useEffect, useRef, useState } from "react";
import jsQR from "jsqr";
import Button from "../ui/Button.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import { useCamera } from "../../hooks/useCamera.js";
import { describeCameraError } from "../../lib/camera.js";
import { describeApiError } from "../../lib/attendanceMessages.js";

const TICK_MS = 250;

/** The QR encodes compact JSON: {"session_id": <int>, "nonce": "<hex>"}. */
function parseQrPayload(text) {
  try {
    const data = JSON.parse(text);
    if (data && Number.isInteger(data.session_id) && typeof data.nonce === "string") {
      return { session_id: data.session_id, nonce: data.nonce };
    }
  } catch {
    /* some other QR code */
  }
  return null;
}

/**
 * Camera view that continuously decodes frames with jsQR until the
 * instructor's projected code is read, then hands the payload to onScan.
 * `busy` pauses decoding while the scan is being submitted on-chain.
 */
export default function QrScannerCard({
  expiresAt,
  windowSeconds = 30,
  busy = false,
  error = null,
  onScan,
  onReverify,
  onCancel,
}) {
  const { videoRef, status, error: cameraError, retry } = useCamera("environment");
  const canvasRef = useRef(null);
  const [hint, setHint] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const busyRef = useRef(busy);
  const lockedRef = useRef(false);

  // Countdown for the verification window (starts when the token was minted).
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // Pause decoding while the submission is in flight; resume after a failure.
  useEffect(() => {
    busyRef.current = busy;
    if (!busy) lockedRef.current = false;
  }, [busy]);

  useEffect(() => {
    if (status !== "ready") return undefined;
    let raf = 0;

    const tick = () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas && video.readyState >= video.HAVE_ENOUGH_DATA && video.videoWidth) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(video, 0, 0);
        try {
          const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const code = jsQR(frame.data, frame.width, frame.height);
          if (code && code.data && !busyRef.current && !lockedRef.current) {
            const payload = parseQrPayload(code.data);
            if (payload) {
              lockedRef.current = true;
              onScan(payload);
            } else {
              setHint("That isn't an attendance code — point at the instructor's projected QR.");
            }
          }
        } catch {
          /* skip an unreadable frame */
        }
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [status, onScan]);

  const secondsLeft = Math.max(0, (expiresAt - now) / 1000);
  const expired = expiresAt > 0 && secondsLeft <= 0;
  const percent = Math.min(100, (secondsLeft / windowSeconds) * 100);

  return (
    <div>
      {error ? (
        <div className="mb-4">
          <StatusBanner tone="error" title="Attendance not locked" detail={error.message}>
            {describeApiError(error)}
          </StatusBanner>
        </div>
      ) : null}

      <div className="relative overflow-hidden rounded-lg bg-ink">
        <video ref={videoRef} playsInline muted autoPlay className="aspect-[4/3] w-full object-cover" />
        {status === "ready" && !busy ? (
          <div className="pointer-events-none absolute inset-6 rounded-lg">
            <span className="absolute left-0 top-0 h-8 w-8 rounded-tl-lg border-l-4 border-t-4 border-white/90" />
            <span className="absolute right-0 top-0 h-8 w-8 rounded-tr-lg border-r-4 border-t-4 border-white/90" />
            <span className="absolute bottom-0 left-0 h-8 w-8 rounded-bl-lg border-b-4 border-l-4 border-white/90" />
            <span className="absolute bottom-0 right-0 h-8 w-8 rounded-br-lg border-b-4 border-r-4 border-white/90" />
            <span className="absolute inset-x-4 h-0.5 animate-scan rounded bg-white/80 shadow-[0_0_12px_rgba(255,255,255,0.9)]" />
          </div>
        ) : null}
        {status === "starting" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/90">
            <Spinner className="h-6 w-6" />
            <p className="text-sm">Starting the camera…</p>
          </div>
        ) : null}
        {busy ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-ink/60 text-white">
            <Spinner className="h-7 w-7" />
            <p className="text-sm font-semibold">Locking attendance on-chain…</p>
          </div>
        ) : null}
      </div>

      {status === "error" ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Camera unavailable">
            {describeCameraError(cameraError)}
          </StatusBanner>
          <div className="mt-3 flex gap-2">
            <Button variant="secondary" onClick={retry}>
              Try Again
            </Button>
            <Button variant="secondary" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4">
          <div>
            <div className="flex items-center justify-between text-xs font-medium text-ink-muted">
              <span>Identity verified — scan inside the window</span>
              <span className={expired ? "font-bold text-danger" : secondsLeft < 10 ? "font-bold text-warning" : ""}>
                {expired ? "window expired" : `${Math.ceil(secondsLeft)}s left`}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
              <div
                className={`h-full rounded-full transition-[width] duration-200 ease-linear ${
                  secondsLeft < 10 ? "bg-warning" : "bg-success"
                }`}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
          {expired ? (
            <div className="mt-3 flex flex-col items-start gap-2">
              <StatusBanner tone="warning">
                Your 30-second verification window has ended — verify your face again.
              </StatusBanner>
              <Button onClick={onReverify}>Verify Face Again</Button>
            </div>
          ) : (
            <div className="mt-4">
              <Button variant="secondary" onClick={onCancel} disabled={busy}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      )}

      {hint && !busy ? (
        <p className="mt-3 text-center text-xs text-ink-muted">{hint}</p>
      ) : null}

      <canvas ref={canvasRef} className="hidden" />
    </div>
  );
}
