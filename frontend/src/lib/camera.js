/** Human-readable text for getUserMedia failures (permission, hardware, context). */
export function describeCameraError(error) {
  const name = error && error.name;
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera access was denied. Allow camera access for this site in your browser settings, then try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return "No usable camera was found on this device.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The camera is busy (another app may be using it). Close it and try again.";
  }
  const detail = (error && error.message) || name || "unknown error";
  return `The camera couldn't be started: ${detail}`;
}
