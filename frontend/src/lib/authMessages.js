/**
 * Maps the backend's structured registration / bootstrap errors
 * ({ check, error }) to human-readable messages for the public pages
 * (/register/*, /setup), keyed "<check>/<error>" exactly like
 * lib/attendanceMessages.js. The raw server message is still shown as the
 * banner's detail line, so a failed demo stays debuggable.
 *
 * Deliberately generic about the institutional email domain and the ID
 * formats: those rules live in the backend config (INSTITUTION_EMAIL_DOMAIN,
 * the whitelist table), never in the client.
 */
const MESSAGES = {
  // Registration (POST /auth/register/hod | /faculty | /student)
  "whitelist/not_whitelisted":
    "Your credentials were not found in the institutional whitelist. Ask the administration to add your employee ID and official email, then register again.",
  "email/invalid_email_domain":
    "That address is not an institutional email — use your official university email.",
  "user/email_already_registered":
    "An account with this email already exists — sign in instead, or register with a different official email.",
  "faculty/employee_id_already_registered":
    "That employee ID is already registered — sign in instead, or ask the administration to check the record.",
  "student/roll_number_already_registered":
    "That roll number is already registered — sign in instead, or ask the administration to check the record.",
  "request/missing_fields": "Fill in every field, then submit again.",

  // System initialization (GET /governance/status, POST /auth/bootstrap/admin)
  "bootstrap/invalid_bootstrap_secret":
    "That bootstrap secret is wrong — it must match the secret configured on the server.",
  "bootstrap/bootstrap_not_configured":
    "Admin bootstrap is disabled on the server — no bootstrap secret is configured.",
  "governance/root_already_locked":
    "The system root is already locked on-chain — the system is initialized, so just sign in.",
  "governance/blockchain_unavailable":
    "The blockchain node couldn't be reached — check that the Hardhat node is running, then retry.",
  "governance/blockchain_not_configured":
    "The governance contract isn't configured on the backend — deploy GovernanceRegistry and set its address.",
  "governance/invalid_root_address":
    "The configured root admin address is invalid — fix it on the backend, then retry.",
  "governance/not_deployer":
    "The backend signer isn't the deployer of the governance contract — only the deployer can lock the root.",
  "governance/blockchain_error":
    "The blockchain rejected the initialization — check the Hardhat node and the contract state, then retry.",

  // Generic fallbacks
  network_error: "Cannot reach the backend — is Flask running on port 5000?",
  missing_fields: "Fill in every field, then submit again.",
};

/**
 * Human-readable text for an ApiError from the registration / bootstrap
 * endpoints. Same lookup chain as describeApiError in attendanceMessages.js.
 */
export function describeAuthError(error) {
  if (!error) return "Something went wrong.";
  const byCheck = error.check ? MESSAGES[`${error.check}/${error.code}`] : null;
  const text = byCheck || MESSAGES[error.code] || error.message;
  return text || "Something went wrong.";
}
