/**
 * Every user-facing string in the student app.
 *
 * `errors` is keyed "<check>/<error>" using the backend's structured error
 * triple ({check, error, message}) so the same code can carry
 * layer-appropriate wording; local (non-HTTP) failures use check "local".
 * describeApiError() falls back to the server's own message.
 */
export const messages = {
  brand: 'EDULEDGER · STUDENT',

  auth: {
    studentsOnly: 'This app is for students',
    unsupportedPlatform:
      'Secure sign-in requires the Android or iOS app. Use Expo Go or a development build.',
    missingToken: 'The server did not return a login token.',
    sessionRestoreFailed: 'Could not restore your session. Please log in again.',
    invalidSession: 'Your saved session is invalid. Please log in again.',
    restoring: 'Restoring your session…',
  },

  storage: {
    secureStoreFailed: 'Unable to access secure storage. Please unlock your device and try again.',
  },

  login: {
    title: 'Welcome back',
    subtitle: 'Log in with your university account.',
    email: 'Email',
    password: 'Password',
    submit: 'Login',
    submitting: 'Logging in…',
    register: 'Create a student account',
    missing: 'Enter your email and password.',
  },

  register: {
    title: 'Create your account',
    subtitle: 'Use your official university details. All fields are required.',
    fields: {
      fullName: 'Full Name',
      rollNumber: 'Roll Number',
      batch: 'Batch',
      department: 'Department',
      email: 'Official University Email',
      password: 'Password',
      confirm: 'Confirm Password',
    },
    submit: 'Create account',
    submitting: 'Creating account…',
    loggingIn: 'Logging in…',
    complete: 'Complete all fields.',
    mismatch: 'Passwords do not match.',
    created:
      'Your account was created. If automatic login fails, continue to Login; do not register again.',
    continueToLogin: 'Continue to Login',
    haveAccount: 'Already registered? Login',
  },

  home: {
    title: 'Your student home',
    subtitle: 'Your university account, all in one place.',
    fullName: 'Full Name',
    rollNumber: 'Roll Number',
    notAvailable: 'Not available',
    markAttendance: 'Mark Attendance',
    markAttendanceHint: 'Verify it’s you, then scan the class QR on the projector.',
    logout: 'Logout',
    logoutFailed: 'Unable to log out. Please try again.',
  },

  setup: {
    title: 'Set up this phone',
    body:
      'One-time setup: this phone is bound to your account and receives a secret that only your biometrics can unlock.',
    busy: 'Setting up this phone…',
    retry: 'Set up this phone',
    ready: 'This phone is set up for attendance.',
    secretPrompt: 'Unlock your EduLedger attendance key',
    secretStoreFailed:
      'The secret could not be saved on this phone. Ask an administrator to rebind your device, then set up again.',
    blocked:
      'This account already has a device secret that this phone does not hold (for example after reinstalling the app). An administrator must rebind your device before this phone can enroll.',
  },

  attendance: {
    title: 'Mark attendance',
    identitySubtitle: 'Step 1 of 2 · Verify it’s really you.',
    scanSubtitle: 'Step 2 of 2 · Point the camera at the class QR.',
    faceScan: 'Face Scan',
    fingerprint: 'Fingerprint',
    iris: 'Iris Scan',
    verify: 'Verify',
    verifying: 'Verifying…',
    checking: 'Checking this phone’s biometrics…',
    verifyPrompt: 'Verify it’s you to mark attendance',
    cancel: 'Cancel',
    noBiometrics:
      'This phone has no enrolled fingerprint or face. Add one in system settings, then try again.',
    noBiometricsHardware: 'This phone has no biometric hardware. Attendance needs a biometric check.',
    cancelled: 'Biometric check cancelled. Try again when you are ready.',
    failed: 'That didn’t work. Try the biometric check again.',
    tokenExpired: 'Your 30-second identity window ended. Verify again to continue.',
    camera: {
      title: 'Camera access',
      body: 'Attendance scanning needs the camera to read the projector QR.',
      grant: 'Allow camera',
      denied: 'Camera access is blocked. Allow it in system settings, or type the code instead.',
      starting: 'Starting the camera…',
    },
    scan: {
      hint: 'Hold the phone steady so the whole QR is visible. The code rotates about every 10 seconds.',
      notAttendanceCode: 'That isn’t an attendance QR. Point at the instructor’s projector.',
      typeCode: 'Type code instead',
      scanQr: 'Scan the QR instead',
      codeLabel: '6-digit code',
      codeHint: 'The number shown beside the QR.',
      sessionLabel: 'Session number (optional)',
      sessionHint: 'Shown as “Session #…” on the projector. Leave blank to let the code find the class.',
      submit: 'Submit code',
      badCode: 'Enter the 6-digit code shown on the projector.',
      sessionInvalid: 'The session number must be digits only.',
      submitting: 'Locking attendance on-chain…',
      secondsLeft: (seconds) => `${seconds}s left`,
    },
    success: {
      title: 'Attendance marked',
      body: 'Your check-in is locked on-chain and cannot be altered.',
      session: 'Session',
      checkedIn: 'Checked in',
      done: 'Done',
    },
    failureTitle: 'Attendance not marked',
    retryScan: 'Scan again',
    restart: 'Verify again',
    home: 'Back to Home',
  },

  errors: {
    // Rotating QR / numeric code (check b)
    'session_nonce/nonce_mismatch': 'QR changed, scan again.',
    'session_nonce/nonce_expired': 'QR changed, scan again.',
    'session_nonce/code_mismatch': 'QR changed, scan again.',
    'session_nonce/ambiguous_code':
      'Two classes show that code right now. Scan the QR instead, or try again in a few seconds.',
    'session_nonce/session_not_found':
      'That attendance session no longer exists. Ask your instructor to start one.',
    'session_nonce/session_inactive':
      'That session is no longer active. Scan the newest QR.',

    // Already present (check d)
    'onchain_lock/attendance_already_locked': 'You’re already marked present.',
    'onchain_lock/blockchain_unavailable':
      'The blockchain node can’t be reached. Ask your instructor to check the Hardhat node, then try again.',
    'onchain_lock/blockchain_error':
      'The blockchain rejected the check-in. Ask your instructor to check the Hardhat node, then try again.',

    // Device binding (check c and the identity step)
    'device_match/device_mismatch':
      'This phone isn’t the device registered for your account. Ask an administrator to rebind it.',
    'device_match/device_not_bound':
      'No device is registered for your account yet. Run first-time setup from Home.',
    'device_match/student_not_found':
      'Your student profile is missing on the server. Ask an administrator to check your account.',
    'identity/device_mismatch':
      'This phone isn’t the device registered for your account. Ask an administrator to rebind it.',

    // Identity token (check a) and the device-identity step
    'verification_token/verification_token_expired':
      'Your identity check expired. Start again from Step 1.',
    'verification_token/verification_token_invalid':
      'Your identity check is no longer valid. Start again from Step 1.',
    'verification_token/student_mismatch':
      'That identity check belongs to another account. Start again from Step 1.',
    'identity/bad_response':
      'The device secret doesn’t match the server. Ask an administrator to rebind your device, then set up again.',
    'identity/challenge_expired': 'Your identity check expired. Start again from Step 1.',
    'identity/challenge_used': 'Your identity check expired. Start again from Step 1.',
    'identity/not_enrolled':
      'This phone isn’t enrolled yet. Run first-time setup from Home.',

    // Session lifecycle
    'session/session_ended':
      'Your instructor ended this session. No more check-ins are accepted.',
    'rate_limit/too_many_attempts':
      'Too many wrong codes. Scan the QR instead, or wait a few minutes.',

    // First-time setup
    'device_enrollment/device_taken':
      'This phone is already bound to another student. Ask an administrator to help.',

    // Local (non-HTTP) failures
    'local/biometric_cancelled': 'Biometric check cancelled. Try again when you are ready.',
    'local/biometric_failed': 'That didn’t work. Try the biometric check again.',
    'local/biometric_unavailable':
      'This phone has no enrolled fingerprint or face. Add one in system settings, then try again.',
    'local/not_enrolled': 'This phone isn’t enrolled yet. Run first-time setup from Home.',
    'local/secret_unreadable':
      'Your device secret could not be unlocked. Run first-time setup from Home, or ask an administrator to rebind this phone.',
    'local/unsupported_platform':
      'Attendance needs the Android or iOS app. This device does not support secure storage.',

    // Shared gateway / network failures (see lib/api.js)
    'network/network_error':
      'Can’t reach the server. Check the API URL, Wi-Fi, and PC firewall.',
    'network/request_timeout': 'The request timed out. Please try again.',
    'configuration/missing_api_url':
      'Set EXPO_PUBLIC_API_URL in mobile/.env and restart Expo.',
    'request/missing_fields': 'Some required fields were missing. Please retry.',
  },
};

/** Human-readable text for an ApiError thrown by lib/api.js. */
export function describeApiError(error) {
  if (!error) return 'Something went wrong.';
  const byCheck = error.check ? messages.errors[`${error.check}/${error.error}`] : null;
  const text = byCheck || messages.errors[error.error] || error.message;
  return text || 'Something went wrong.';
}
