import { useSyncExternalStore } from 'react';
import * as SecureStore from 'expo-secure-store';

import { api, ApiError, configureAuth } from './api';
import { messages } from './messages';

const SESSION_KEY = 'eduledger.session.v1';
const STUDENTS_ONLY = messages.auth.studentsOnly;
let token = null;
/** @type {{ user: null | { id: number, role: string, fullName: string }, profile: null | { rollNumber: string }, loading: boolean, message: string, signOutId: number }} */
let state = { user: null, profile: null, loading: true, message: '', signOutId: 0 };
const listeners = new Set();
let restorePromise;
let storageQueue = Promise.resolve();

function update(next) {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Serialize writes/deletions so logging out during /auth/me cannot restore a token.
function mutateStorage(operation) {
  const pending = storageQueue.then(operation);
  storageQueue = pending.catch(() => {});
  return pending;
}

function storageError() {
  return new ApiError(0, {
    check: 'storage', error: 'secure_store_failed',
    message: messages.storage.secureStoreFailed,
  });
}

async function requireSecureStore() {
  if (!(await SecureStore.isAvailableAsync())) {
    throw new ApiError(0, {
      check: 'storage', error: 'unsupported_platform',
      message: messages.auth.unsupportedPlatform,
    });
  }
}

export function clearMessage() {
  update({ message: '' });
}

export async function logout(message = '') {
  token = null;
  update({ user: null, profile: null, loading: false, message, signOutId: state.signOutId + 1 });
  try {
    await mutateStorage(() => SecureStore.deleteItemAsync(SESSION_KEY));
  } catch {
    const error = storageError();
    update({ message: [message, error.message].filter(Boolean).join('\n') });
    throw error;
  }
}

async function requireStudent(user) {
  if (user?.role !== 'STUDENT') {
    await logout(STUDENTS_ONLY);
    throw new ApiError(403, {
      check: 'auth', error: 'students_only', message: STUDENTS_ONLY,
    });
  }
}

export async function loadUser() {
  const requestToken = token;
  if (!requestToken) return null;
  const result = await api('/auth/me');
  if (token !== requestToken) return null;
  await requireStudent(result.user);
  try {
    await mutateStorage(() => token === requestToken
      ? SecureStore.setItemAsync(SESSION_KEY, JSON.stringify({
        token: requestToken, user: result.user, profile: result.profile,
      }))
      : Promise.resolve());
  } catch {
    throw storageError();
  }
  if (token !== requestToken) return null;
  update({ user: result.user, profile: result.profile, loading: false, message: '' });
  return result.user;
}

export async function login(email, password) {
  clearMessage();
  await requireSecureStore();
  const result = await api('/auth/login', {
    method: 'POST', auth: false, body: { email: email.trim(), password },
  });
  await requireStudent(result.user);
  if (typeof result.token !== 'string' || !result.token) {
    throw new ApiError(0, {
      check: 'auth', error: 'missing_token', message: messages.auth.missingToken,
    });
  }
  token = result.token;
  try {
    // Login does not return the student profile. /auth/me supplies rollNumber.
    return await loadUser();
  } catch (error) {
    if (token === result.token) await logout(error.message);
    throw error;
  }
}

export function restoreSession() {
  if (!restorePromise) {
    restorePromise = (async () => {
      try {
        await requireSecureStore();
        const saved = await SecureStore.getItemAsync(SESSION_KEY);
        if (!saved) {
          update({ loading: false });
          return;
        }
        const session = JSON.parse(saved);
        await requireStudent(session.user);
        if (typeof session.token !== 'string' || !session.token) {
          throw new Error(messages.auth.invalidSession);
        }
        token = session.token;
        // Cached identity is never enough to unlock Home; validate with the API.
        await loadUser();
      } catch (error) {
        if (error.error === 'unsupported_platform') {
          update({ loading: false, message: error.message });
        } else if (state.loading || token) {
          await logout(error instanceof ApiError
            ? error.message
            : messages.auth.sessionRestoreFailed).catch(() => {});
        }
      }
    })();
  }
  return restorePromise;
}

configureAuth({
  getToken: () => token,
  onUnauthorized: async (error, requestToken) => {
    if (requestToken && requestToken !== token) return;
    // Keep the backend ApiError intact even if native storage deletion fails.
    await logout(error.message).catch(() => {});
  },
});

export function useAuth() {
  const snapshot = useSyncExternalStore(subscribe, () => state, () => state);
  return { ...snapshot, login, loadUser, logout, clearMessage };
}
