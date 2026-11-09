import * as Crypto from 'expo-crypto';
import * as LocalAuthentication from 'expo-local-authentication';

import { api, ApiError } from './api';
import { getDeviceId, readDeviceSecret } from './device';
import { messages } from './messages';

const BLOCK_BYTES = 64;
const IPAD = 0x36;
const OPAD = 0x5c;

function localError(error, message) {
  return new ApiError(0, { check: 'local', error, message });
}

// Hermes does not guarantee TextEncoder, and every value here is ASCII
// (base64url secret/challenge and a UUID), so encode UTF-8 by hand.
function utf8Bytes(text) {
  const bytes = [];
  for (let index = 0; index < text.length; index += 1) {
    let code = text.codePointAt(index);
    if (code > 0xffff) index += 1;
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f),
      );
    }
  }
  return Uint8Array.from(bytes);
}

async function sha256(bytes) {
  const digest = await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes);
  return new Uint8Array(digest);
}

/**
 * HMAC-SHA256(secret, message) as lowercase hex - the same value the backend
 * computes with hmac.new(secret.encode(), message.encode(), sha256). Only the
 * block compression is done here; every hash comes from expo-crypto.
 */
export async function hmacSha256Hex(secret, message) {
  let key = utf8Bytes(secret);
  if (key.length > BLOCK_BYTES) key = await sha256(key);

  const innerPad = new Uint8Array(BLOCK_BYTES);
  const outerPad = new Uint8Array(BLOCK_BYTES);
  for (let index = 0; index < BLOCK_BYTES; index += 1) {
    const byte = key[index] ?? 0;
    innerPad[index] = byte ^ IPAD;
    outerPad[index] = byte ^ OPAD;
  }

  const payload = utf8Bytes(message);
  const innerInput = new Uint8Array(BLOCK_BYTES + payload.length);
  innerInput.set(innerPad, 0);
  innerInput.set(payload, BLOCK_BYTES);

  const innerHash = await sha256(innerInput);
  const outerInput = new Uint8Array(BLOCK_BYTES + innerHash.length);
  outerInput.set(outerPad, 0);
  outerInput.set(innerHash, BLOCK_BYTES);

  const digest = await sha256(outerInput);
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** What this phone can offer on the Step 1 screen. */
export async function describeBiometrics() {
  const [hasHardware, enrolled, types] = await Promise.all([
    LocalAuthentication.hasHardwareAsync(),
    LocalAuthentication.isEnrolledAsync(),
    LocalAuthentication.supportedAuthenticationTypesAsync(),
  ]);
  return {
    hasHardware,
    enrolled,
    face: types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION),
    fingerprint: types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT),
    iris: types.includes(LocalAuthentication.AuthenticationType.IRIS),
  };
}

/**
 * Run the OS biometric prompt. LocalAuthentication cannot select a modality,
 * so the Face/Fingerprint buttons only decide which label the student tapped -
 * the platform prompts for whatever is enrolled.
 */
export async function authenticateBiometric() {
  let result;
  try {
    result = await LocalAuthentication.authenticateAsync({
      promptMessage: messages.attendance.verifyPrompt,
      cancelLabel: messages.attendance.cancel,
      fallbackLabel: '',
      // D4: the secret is released by biometrics, not by the device passcode.
      disableDeviceFallback: true,
    });
  } catch {
    throw localError('biometric_failed', messages.errors['local/biometric_failed']);
  }
  if (result.success) return;
  const cancelled = ['user_cancel', 'system_cancel', 'app_cancel', 'user_fallback']
    .includes(result.error);
  if (cancelled) {
    throw localError('biometric_cancelled', messages.errors['local/biometric_cancelled']);
  }
  if (['not_enrolled', 'not_available', 'passcode_not_set'].includes(result.error)) {
    throw localError('biometric_unavailable', messages.errors['local/biometric_unavailable']);
  }
  throw localError('biometric_failed', messages.errors['local/biometric_failed']);
}

/**
 * Step 1: biometric prompt -> read the secret -> single-use challenge ->
 * HMAC response -> 30 s identity token. The challenge is fetched after the
 * prompt so its short TTL is not spent while the student is authenticating.
 */
export async function verifyDeviceIdentity() {
  const deviceId = await getDeviceId();
  const secret = await readDeviceSecret();
  const challengeResponse = await api('/students/identity/challenge');
  const { challenge } = challengeResponse;
  if (typeof challenge !== 'string' || !challenge) {
    throw localError('challenge_missing', messages.errors['identity/challenge_expired']);
  }

  const response = await hmacSha256Hex(secret, `${challenge}:${deviceId}`);
  const result = await api('/students/verify-identity-device', {
    method: 'POST',
    body: { device_id: deviceId, challenge, response },
  });
  const reported = Number(result.expires_in ?? challengeResponse.expires_in);
  const expiresIn = Number.isFinite(reported) && reported > 0 ? reported : 30;
  return {
    token: result.verification_token,
    deviceId,
    expiresAt: Date.now() + expiresIn * 1000,
    expiresIn,
  };
}

/** The QR the instructor projects is compact JSON: {"session_id": 3, "nonce": "…"}. */
export function parseQrPayload(text) {
  try {
    const data = JSON.parse(text);
    if (
      data && Number.isInteger(data.session_id) && data.session_id > 0
      && typeof data.nonce === 'string' && data.nonce
    ) {
      return { sessionId: data.session_id, nonce: data.nonce };
    }
  } catch {
    /* not JSON - some other QR code */
  }
  return null;
}

/**
 * Step 2: POST /attendance/scan. QR scans send session_id + nonce; a typed
 * code sends numeric_code, with session_id only when the student filled it in
 * (the backend resolves a code alone to the one live session showing it).
 */
export async function submitAttendanceScan({
  token, deviceId, sessionId, nonce, numericCode,
}) {
  const body = { verification_token: token, device_id: deviceId };
  if (nonce) {
    body.session_id = sessionId;
    body.nonce = nonce;
  } else {
    body.numeric_code = numericCode;
    if (sessionId) body.session_id = sessionId;
  }
  return api('/attendance/scan', { method: 'POST', body });
}
