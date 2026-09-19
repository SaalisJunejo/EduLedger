import { useRef } from "react";
import Button from "../ui/Button.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import { useCamera } from "../../hooks/useCamera.js";
import { describeCameraError } from "../../lib/camera.js";

/**
 * Live camera preview with a capture button — the captured frame becomes the
 * face image sent to verify-identity.
 */
export default function FaceCaptureCard({ onCapture, onCancel }) {
  const { videoRef, status, error, retry } = useCamera("user");
  const canvasRef = useRef(null);

  function capture() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    onCapture(canvas.toDataURL("image/png"));
  }

  return (
    <div>
      <div className="relative overflow-hidden rounded-lg bg-ink">
        <video ref={videoRef} playsInline muted autoPlay className="aspect-[4/3] w-full object-cover" />
        {status === "starting" ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/90">
            <Spinner className="h-6 w-6" />
            <p className="text-sm">Starting the camera…</p>
          </div>
        ) : null}
      </div>

      {status === "error" ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Camera unavailable">
            {describeCameraError(error)}
          </StatusBanner>
          <div className="mt-3 flex gap-2">
            <Button variant="secondary" onClick={retry}>
              Try Again
            </Button>
            <Button variant="secondary" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex items-center justify-between gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={status !== "ready"}>
            Cancel
          </Button>
          <Button onClick={capture} disabled={status !== "ready"}>
            Capture Photo
          </Button>
        </div>
      )}
      <canvas ref={canvasRef} className="hidden" />
    </div>
  );
}
