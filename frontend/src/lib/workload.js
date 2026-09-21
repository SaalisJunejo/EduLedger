/**
 * Faculty workload (Module 3) frontend helpers — the counterpart of
 * lib/records.js for the instructor's "My Classes" tab and the admin
 * workload audit log.
 *
 * There is no class-management UI or timetable ingestion yet (Phase 4+), so
 * the professor's roster for today is a mock mirroring the ScheduledClass
 * rows seeded by backend/seed_workload_demo.py — the same approach as the
 * Phase 2.4 attendance class dropdown in InstructorPage.
 */

// The professor "logged in" for the demo. No auth layer exists yet — the
// faculty endpoints take the id in the request body, exactly like every
// other module.
export const PROFESSOR_ID = 101;

// Professor directory for the substitute flows (the backend stores opaque
// professor ids only; names are demo flavor for the mock roster).
export const PROFESSORS = [
  { id: 101, name: "Dr. Sana Raza" },
  { id: 102, name: "Dr. Ayesha Khan" },
  { id: 103, name: "Dr. Bilal Ahmed" },
];

export function professorLabel(id) {
  const professor = PROFESSORS.find((entry) => entry.id === Number(id));
  return professor ? `Prof. ${professor.id} · ${professor.name}` : `Prof. ${id}`;
}

// Today's timetable for the logged-in professor (mock — the ids must match
// the ScheduledClass rows in the database; see seed_workload_demo.py, which
// prints the start times these labels mirror).
export const MOCK_CLASSES = [
  { id: 1, courseId: "BSCS-401", title: "Distributed Systems", room: "A-101", start: "02:52", professorId: 101 },
  { id: 2, courseId: "BSCS-402", title: "Blockchain Engineering", room: "B-204", start: "05:52", professorId: 101 },
  { id: 3, courseId: "BSCS-501", title: "Information Security", room: "C-301", start: "08:52", professorId: 101 },
];

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
  try {
    return new Date(iso).toLocaleString(undefined, {
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
    "This key wasn't issued by the API — only keys from “Issue Substitute Key” can be redeemed.",
  "substitute_token/token_already_redeemed":
    "This key was already redeemed — each key starts the class exactly once.",

  // Substitute identity — check "substitute"
  "substitute/substitute_mismatch":
    "This key authorizes a different substitute — redeem it while acting as the professor it was issued for.",

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
