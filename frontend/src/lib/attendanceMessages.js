/**
 * Maps the backend's structured attendance/identity errors
 * ({ check, error }) to human-readable messages for the student UI.
 *
 * Every backend rejection names its check — the keys below are
 * "<check>/<error>" so the same error code from two layers (e.g.
 * device_mismatch at verify-identity vs at /attendance/scan) can carry
 * layer-appropriate wording. The raw server message is still shown as a
 * small detail line, so failures stay debuggable during demos.
 */
const MESSAGES = {
  // Identity verification (POST /attendance/verify-identity)
  "identity/identity_verification_failed":
    "We couldn't verify your face. Try again in better lighting — if it keeps failing, an admin has to re-enroll your face template.",
  "identity/face_not_enrolled":
    "Your face isn't enrolled yet. Enroll it with the photo you just captured (demo enrollment).",
  "identity/student_not_found":
    "Your student profile doesn't exist on the backend yet. Enroll with the photo you just captured to create it.",
  "identity/invalid_face_image":
    "That image couldn't be processed. Capture the photo again.",
  "identity/embedding_provider_mismatch":
    "Your stored face template uses a different embedding provider — re-enroll your face with this photo.",

  // Verification token (checked at POST /attendance/scan)
  "verification_token/verification_token_invalid":
    "Your verification is no longer valid (30-second limit). Verify your face again.",
  "verification_token/verification_token_expired":
    "Your verification timed out (30-second limit). Verify your face again.",

  // Session + rotating nonce (check b)
  "session_nonce/nonce_mismatch":
    "The QR code had already rotated — point at the projector and scan again quickly.",
  "session_nonce/nonce_expired":
    "That code expired — scan the QR again.",
  "session_nonce/session_not_found":
    "This attendance session doesn't exist (anymore). Ask your instructor to start one.",
  "session_nonce/session_inactive":
    "This attendance session has ended — a newer one may have started. Scan the latest QR.",

  // Device binding (check c)
  "device_match/device_mismatch":
    "This device doesn't match the device registered for you. If you changed devices, an admin has to rebind it.",
  "device_match/device_not_bound":
    "No device is registered for you yet — reload this page to register this device.",
  "device_match/student_not_found":
    "Your student profile disappeared from the backend. Re-enroll your face below.",

  // On-chain lock (check d)
  "onchain_lock/attendance_already_locked":
    "Your attendance for this session is already locked on-chain — you're marked present.",
  "onchain_lock/blockchain_unavailable":
    "The blockchain node couldn't be reached. Check that the Hardhat node is running, then try again.",
  "onchain_lock/blockchain_error":
    "The blockchain rejected the attendance lock. Check the Hardhat node, then try again.",

  // Device registration (page load)
  "device_binding/device_already_bound":
    "A different device is already registered for this student.",
  "device_binding/device_taken":
    "This device ID is already bound to another student.",
  "device_binding/student_not_found":
    "Student profile not found — this device will be registered automatically once your face is enrolled.",

  // Generic fallbacks
  network_error: "Cannot reach the backend — is Flask running on port 5000?",
  missing_fields: "Some required fields were missing — please retry.",
};

/**
 * Human-readable text for an ApiError thrown by lib/api.js.
 * Falls back to the server's own message when no mapping exists.
 */
export function describeApiError(error) {
  if (!error) return "Something went wrong.";
  const byCheck = error.check ? MESSAGES[`${error.check}/${error.code}`] : null;
  const text = byCheck || MESSAGES[error.code] || error.message;
  return text || "Something went wrong.";
}
