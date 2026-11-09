import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import { api, ApiError } from './api';
import { messages } from './messages';

// The device id is not a secret: the server stores it next to the account so
// the HMAC response is bound to this phone. Same value the web dev client sends.
const DEVICE_ID_KEY = 'eduledger.device.id.v1';
// The HMAC secret is returned once by /students/device/enroll. It is only read
// after LocalAuthentication succeeds and (where supported) behind the OS
// biometric prompt, never on Home's setup check.
const SECRET_KEY = 'eduledger.device.secret.v1';
// Plain marker so Home can tell "set up" from "not set up" without prompting.
const ENROLLED_KEY = 'eduledger.device.enrolled.v1';

let setupPromise = null;

function localError(error, message) {
  return new ApiError(0, { check: 'local', error, message });
}

export function canProtectSecret() {
  try {
    return Boolean(SecureStore.canUseBiometricAuthentication?.());
  } catch {
    return false;
  }
}

function secretOptions() {
  if (!canProtectSecret()) return {};
  return {
    requireAuthentication: true,
    authenticationPrompt: messages.setup.secretPrompt,
    // Never migrate the secret through an encrypted backup.
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  };
}

export async function getDeviceId() {
  if (!(await SecureStore.isAvailableAsync())) {
    throw localError('unsupported_platform', messages.errors['local/unsupported_platform']);
  }
  const saved = await SecureStore.getItemAsync(DEVICE_ID_KEY);
  if (saved) return saved;
  const created = Crypto.randomUUID();
  await SecureStore.setItemAsync(DEVICE_ID_KEY, created);
  return created;
}

/** True when this phone holds the credential and the server issued it. */
export async function isEnrolled() {
  try {
    const marker = await SecureStore.getItemAsync(ENROLLED_KEY);
    if (!marker) return false;
    const deviceId = await SecureStore.getItemAsync(DEVICE_ID_KEY);
    return Boolean(deviceId) && JSON.parse(marker)?.device_id === deviceId;
  } catch {
    return false;
  }
}

async function enrollOnce() {
  const deviceId = await getDeviceId();
  // Bind first (idempotent when this phone is already the bound device), then
  // ask the server for the one-time secret.
  await api('/student/register-device', { method: 'POST', body: { device_id: deviceId } });
  const result = await api('/students/device/enroll', {
    method: 'POST', body: { device_id: deviceId },
  });
  if (typeof result.device_secret !== 'string' || !result.device_secret) {
    throw localError('secret_missing', messages.setup.secretStoreFailed);
  }

  const protectedSecret = canProtectSecret();
  try {
    await SecureStore.setItemAsync(SECRET_KEY, result.device_secret, secretOptions());
  } catch {
    // The server already committed the secret and will never return it again,
    // so recovering needs an administrator rebind (POST /admin/rebind-device).
    throw localError('secret_store_failed', messages.setup.secretStoreFailed);
  }
  await SecureStore.setItemAsync(
    ENROLLED_KEY,
    JSON.stringify({ device_id: deviceId, biometric_protected: protectedSecret }),
  );
  return { deviceId, protectedSecret };
}

/**
 * One-time first-time setup: bind this phone and store its device secret.
 * Concurrent callers (Home mount + a retry tap) share one enrollment, because
 * a second /students/device/enroll would only return 409.
 */
export function enrollDevice() {
  if (!setupPromise) {
    setupPromise = enrollOnce().finally(() => { setupPromise = null; });
  }
  return setupPromise;
}

/** Read the secret; this is where the OS releases it after the biometric prompt. */
export async function readDeviceSecret() {
  let secret = null;
  try {
    secret = await SecureStore.getItemAsync(SECRET_KEY, secretOptions());
  } catch {
    throw localError('secret_unreadable', messages.errors['local/secret_unreadable']);
  }
  if (!secret) {
    throw localError('not_enrolled', messages.errors['local/not_enrolled']);
  }
  return secret;
}
