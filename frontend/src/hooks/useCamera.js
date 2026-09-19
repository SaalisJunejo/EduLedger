import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Opens a camera stream and attaches it to a <video> element (the hook
 * provides the videoRef). Handles permission/absence errors and stops the
 * stream on unmount. `retry()` re-runs the whole acquisition.
 */
export function useCamera(facingMode = "user") {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [status, setStatus] = useState("starting"); // starting | ready | error
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setStatus("starting");
    setError(null);

    (async () => {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        if (!cancelled) {
          setStatus("error");
          setError(new Error("this browser/context doesn't expose a camera API (a secure origin is required)"));
        }
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          try {
            await video.play();
          } catch {
            /* autoplay policy — the element is muted + playsInline */
          }
        }
        setStatus("ready");
      } catch (err) {
        if (!cancelled) {
          setStatus("error");
          setError(err);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, [facingMode, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return { videoRef, status, error, retry };
}
