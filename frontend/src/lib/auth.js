import { api } from "./api.js";

const TOKEN_KEY = "eduledger.auth_token";
const ROLE_PATHS = new Map([
  ["ADMIN", "/admin"],
  ["HOD", "/hod"],
  ["FACULTY", "/instructor"],
  ["STUDENT", "/student"],
]);

// If browser storage is blocked, keep authentication for this tab only.
let sessionToken = null;
let storageUnavailable = false;

export function getToken() {
  if (storageUnavailable) return sessionToken;
  try {
    return window.localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    storageUnavailable = true;
    return sessionToken;
  }
}

function storeToken(token) {
  sessionToken = token;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    storageUnavailable = true;
  }
}

export function logout() {
  sessionToken = null;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    storageUnavailable = true;
  }
}

export function getRolePath(role) {
  return ROLE_PATHS.get(role) || null;
}

export async function login(email, password) {
  const data = await api("/auth/login", {
    method: "POST",
    body: { email, password },
  });
  if (!data?.token || !getRolePath(data.user?.role)) {
    logout();
    throw new Error("The login response did not include a token and a supported account role.");
  }
  storeToken(data.token);
  return data.user;
}

export async function loadUser() {
  const data = await api("/auth/me");
  if (!data?.user || !getRolePath(data.user.role)) {
    throw new Error("Your account does not have a supported dashboard role.");
  }
  return data.user;
}
