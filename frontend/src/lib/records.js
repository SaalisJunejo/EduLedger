/**
 * Audit-trail (records) frontend helpers — the Module 2 counterpart of
 * lib/api.js + lib/attendanceMessages.js.
 *
 * POST /api/v1/ipfs/upload is multipart/form-data, which lib/api.js (JSON
 * only) can't express, so the evidence upload gets its own transport here.
 * Failures still throw ApiError, so every records/IPFS error flows through
 * describeRecordsError and the shared StatusBanner pattern like the
 * attendance module.
 */
import { ApiError } from "./api.js";

/**
 * URL for an evidence CID on the local Kubo gateway (the daemon the backend
 * pins to first). The upload endpoint also returns a public ipfs.io gateway
 * URL, but only the local gateway reliably serves CIDs pinned on this machine.
 */
export function ipfsGatewayUrl(cid) {
  return `http://localhost:8080/ipfs/${cid}`;
}

/**
 * Pin an evidence file on IPFS via POST /api/v1/ipfs/upload.
 *
 * Returns the upload payload `{ cid, gateway_url }`; throws ApiError carrying
 * the backend's {error, check, message} shape on any failure.
 */
export async function uploadEvidence(file) {
  const formData = new FormData();
  formData.append("file", file);

  let response;
  try {
    // No Content-Type header: the browser must set the multipart boundary.
    response = await fetch("/api/v1/ipfs/upload", { method: "POST", body: formData });
  } catch {
    throw new ApiError(0, {
      error: "network_error",
      message: "Cannot reach the backend — is Flask running on port 5000?",
    });
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body (e.g. an empty response) */
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload;
}

// Evidence policy, mirrored from backend/app/api/ipfs.py so the form can
// reject wrong types / oversized files before touching the network.
export const EVIDENCE_MAX_BYTES = 10 * 1024 * 1024;
export const EVIDENCE_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg"];

// Human-readable text for the records + IPFS endpoints, keyed "<check>/<error>"
// like attendanceMessages.js. The raw server message still shows as the
// banner's detail line, so contract reverts stay debuggable during demos.
const MESSAGES = {
  // Evidence upload — check "request" (validation) or "ipfs" (pinning failed)
  "request/missing_fields": "Attach the evidence file — PDF, PNG or JPG, up to 10 MB.",
  "request/invalid_file_type": "That file type isn't accepted — evidence must be a PDF, PNG or JPG.",
  "request/file_too_large": "That file is over the 10 MB evidence limit — attach a smaller one.",
  "request/invalid_file": "The selected file is empty — pick a valid evidence document.",
  "ipfs/ipfs_daemon_unreachable":
    "The local IPFS daemon isn't running — start it with `ipfs daemon`, then retry the upload.",
  "ipfs/ipfs_upload_failed":
    "The evidence couldn't be pinned — both the local IPFS daemon and the Web3.Storage fallback failed.",
  "ipfs/web3_storage_not_configured":
    "The IPFS daemon is unreachable and the Web3.Storage fallback isn't configured — start `ipfs daemon` and retry.",

  // Propose / approve — check "role", "proposal", "audit_trail" or "database"
  "request/empty_field": "The record field can't be empty.",
  "request/empty_ipfs_cid": "The evidence CID is missing — upload the evidence file again.",
  "role/not_instructor":
    "The backend's instructor signer isn't authorized to propose changes — check the INSTRUCTOR_ROLE grant on the audit-trail contract.",
  "role/not_an_approver":
    "This signer isn't authorized to approve changes — check the HOD / EXAM_CONTROLLER role grants on the audit-trail contract.",
  "role/invalid_role": "Invalid approver role.",
  "proposal/proposal_not_found": "That proposal doesn't exist on-chain (anymore).",
  "proposal/proposal_not_pending": "This proposal was already executed — it can't be signed again.",
  "proposal/already_signed": "This role already signed the proposal — the other approver's signature is still needed.",
  "audit_trail/blockchain_unavailable":
    "The blockchain node couldn't be reached — check that the Hardhat node is running, then retry.",
  "audit_trail/audit_trail_not_configured":
    "The audit-trail contract isn't configured on the backend — deploy it and set AUDIT_TRAIL_CONTRACT_ADDRESS.",
  "audit_trail/blockchain_error":
    "The blockchain rejected the transaction — check the Hardhat node and the contract state, then retry.",
  "database/proposal_mirror_failed":
    "The change is recorded on-chain, but the local database mirror couldn't be updated — the chain remains the source of truth.",

  // Generic fallbacks
  network_error: "Cannot reach the backend — is Flask running on port 5000?",
  missing_fields: "Some required information is missing — fill in the fields and attach the evidence file.",
};

/**
 * Human-readable text for an ApiError from the records / IPFS endpoints.
 * Same lookup chain as describeApiError in attendanceMessages.js.
 */
export function describeRecordsError(error) {
  if (!error) return "Something went wrong.";
  const byCheck = error.check ? MESSAGES[`${error.check}/${error.code}`] : null;
  const text = byCheck || MESSAGES[error.code] || error.message;
  return text || "Something went wrong.";
}
