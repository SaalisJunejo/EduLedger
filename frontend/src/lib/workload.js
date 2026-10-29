/**
 * Faculty workload (Module 3) frontend helpers — the counterpart of
 * lib/records.js for the instructor's "My Classes" tab and the admin
 * workload audit log.
 */
import { api } from "./api.js";

export async function loadFacultyClasses() {
  const data = await api("/faculty/classes");
  return data.classes;
}

// Institution-wide class list for the HOD / Admin surfaces (HOD and ADMIN
// only). Unlike loadFacultyClasses() each row also names its professor.
export async function loadTimetableClasses(professorId) {
  const query = professorId ? `?professor_id=${encodeURIComponent(professorId)}` : "";
  const data = await api(`/timetable/classes${query}`);
  return data.classes;
}

export async function loadFacultyColleagues() {
  const data = await api("/faculty/colleagues");
  return data.colleagues || [];
}

// Use returned names when available, and a real user ID as the fallback.
export function professorLabel(id) {
  return id == null ? "Not available" : `Faculty #${id}`;
}

// SessionLog.status -> badge label + chip classes (the design foundation's
// chip recipe: rounded-full bg-*-soft px-2.5 py-1 text-xs font-bold text-*).
export const STATUS_META = {
  CONDUCTED: { label: "Conducted ✅", chip: "bg-success-soft text-success" },
  CONDUCTED_ZERO_STUDENTS: { label: "Conducted — Zero Attendance", chip: "bg-warning-soft text-warning" },
  UNCONDUCTED: { label: "Unconducted", chip: "bg-danger-soft text-danger-strong" },
};

export function statusMeta(status) {
  return STATUS_META[status] || { label: status, chip: "bg-surface-muted text-ink-muted" };
}

// "0x1234abcd…" style abbreviation for tx hashes in tight table rows.
export function shortHash(hash) {
  if (!hash) return "";
  return hash.length > 22 ? `${hash.slice(0, 10)}…${hash.slice(-8)}` : hash;
}

// Abbreviation of a substitute key (the JWT is ~150 chars; the full token
// always renders in its own copyable block next to this short code).
export function shortToken(token) {
  if (!token) return "";
  return `${token.slice(0, 10)}…${token.slice(-6)}`;
}

export function formatDateTime(iso) {
  if (!iso) return "Not available";
  try {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("en-US", {
      timeZone: "Asia/Karachi",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// Human-readable text for the faculty workload endpoints, keyed
// "<check>/<error>" like attendanceMessages.js / records.js. The raw server
// message still shows as the banner's detail line, so contract reverts stay
// debuggable during demos.
const MESSAGES = {
  "schedule/too_early": "It's too early to start this class. Wait until the scheduled start window, then try again.",

  // Input validation — check "request"
  "request/missing_fields": "Some required information is missing — check the fields and retry.",
  "request/invalid_substitute": "Pick a colleague other than yourself as the substitute.",

  // Unknown class — check "class"
  "class/class_not_found":
    "This class doesn't exist on the backend (anymore). If the demo database was reset, re-run seed_workload_demo.py.",

  // Ownership — check "professor"
  "professor/not_class_professor":
    "That class is scheduled for a different professor — you can't start it or issue keys for it.",
  "professor/not_session_conductor":
    "You're not the live conductor of this session — a substitute may be covering the class.",

  // Zero-attendance session gates — check "session"
  "session/session_not_started":
    "Start the session first — zero attendance can only be declared for a class whose session is live.",
  "session/session_inactive": "The live session ended — start a new session for this class.",

  // Class already concluded — check "session_log" (one entry per class on-chain)
  "session_log/class_already_logged":
    "This class already has a final workload entry on-chain — one entry per class, it can't be re-logged.",
  "session_log/workload_already_logged":
    "This class already has a final workload entry on-chain — one entry per class, it can't be re-logged.",

  // QR session gates — check "workload_session" (only hit when a plain
  // attendance session's id is submitted to a workload endpoint)
  "workload_session/not_a_workload_session":
    "That session wasn't started via the faculty workload module.",

  // Substitute key validity — check "substitute_token"
  "substitute_token/substitute_token_invalid":
    "That isn't a valid substitute key — copy the full token from the issuing professor and paste it again.",
  "substitute_token/substitute_token_expired":
    "This substitute key expired (30-minute lifetime). Ask the professor to issue a fresh one.",
  "substitute_token/token_not_found":
    "This key wasn't issued by the API — ask the primary professor to issue a key from the Substitute card.",
  "substitute_token/substitute_token_wrong_purpose":
    "This isn't a substitute key. Ask the primary professor for the full key from the Substitute card.",
  "substitute_token/token_already_redeemed":
    "This key was already redeemed — each key starts the class exactly once.",

  // Substitute identity — check "substitute"
  "substitute/substitute_mismatch":
    "This key was issued to a different colleague. Ask the primary professor for a key issued to your account.",
  "substitute/substitute_not_found":
    "That colleague no longer exists. Refresh the page and select another colleague.",
  "substitute/substitute_not_faculty":
    "Only a faculty colleague can cover this class. Refresh the page and select another colleague.",

  // On-chain anchoring — check "workload_ledger"
  "workload_ledger/blockchain_unavailable":
    "The blockchain node couldn't be reached — check that the Hardhat node is running, then retry.",
  "workload_ledger/workload_ledger_not_configured":
    "The workload contract isn't configured on the backend — deploy it and set WORKLOAD_LEDGER_CONTRACT_ADDRESS.",
  "workload_ledger/blockchain_error":
    "The blockchain rejected the transaction — check the Hardhat node and the contract state, then retry.",

  // Generic fallbacks
  network_error: "Cannot reach the backend — is Flask running on port 5000?",
  missing_fields: "Some required information is missing — fill in the fields and retry.",
};

/**
 * Human-readable text for an ApiError from the faculty workload endpoints.
 * Same lookup chain as describeApiError / describeRecordsError.
 */
export function describeWorkloadError(error) {
  if (!error) return "Something went wrong.";
  const byCheck = error.check ? MESSAGES[`${error.check}/${error.code}`] : null;
  const text = byCheck || MESSAGES[error.code] || error.message;
  return text || "Something went wrong.";
}
