import { useRef, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import { api } from "../../lib/api.js";
import {
  EVIDENCE_EXTENSIONS,
  EVIDENCE_MAX_BYTES,
  describeRecordsError,
  ipfsGatewayUrl,
  uploadEvidence,
} from "../../lib/records.js";

// Record fields an instructor can propose changing. Kept deliberately small
// for the MVP — the contract stores the field as an opaque string.
const FIELDS = [
  { value: "grade", label: "Grade" },
  { value: "attendance", label: "Attendance" },
];

// Submit flow: pin the evidence on IPFS first, then anchor the proposal
// on-chain (the backend requires the CID inside proposeChange).
const PHASE = { IDLE: "idle", UPLOADING: "uploading", PROPOSING: "proposing", SUCCESS: "success" };

const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const FILE_INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-primary-soft file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-primary-strong disabled:opacity-60";

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * Instructor's "Propose a record change" form (Module 2 audit trail).
 *
 * Submitting runs a two-step flow with distinct loading states: the evidence
 * file is pinned on IPFS (POST /ipfs/upload), then the proposal anchors
 * on-chain with that CID (POST /records/propose). The contract counts the
 * proposer as the first signature, so a fresh proposal is always "1 of 2" —
 * the HOD or Exam Controller co-sign executes it.
 */
export default function ProposeChangeCard() {
  const [studentId, setStudentId] = useState("");
  const [field, setField] = useState(FIELDS[0].value);
  const [oldValue, setOldValue] = useState("");
  const [newValue, setNewValue] = useState("");
  const [file, setFile] = useState(null);
  const [phase, setPhase] = useState(PHASE.IDLE);
  const [failedPhase, setFailedPhase] = useState(null); // "upload" | "propose" | null (validation)
  const [error, setError] = useState(null); // ApiError or { message } for validation
  const [pinnedCid, setPinnedCid] = useState(null);
  const [result, setResult] = useState(null); // propose response + pinned CID

  const fileInputRef = useRef(null);
  const busy = phase === PHASE.UPLOADING || phase === PHASE.PROPOSING;

  // Client-side mirror of the backend's validation, so wrong types / oversized
  // files fail instantly with the same wording the API would answer with.
  function validate() {
    if (!studentId.trim() || !Number.isInteger(Number(studentId)) || Number(studentId) <= 0) {
      return "Enter the student's ID — a positive whole number.";
    }
    if (!newValue.trim()) return "Enter the new value for the record.";
    if (!file) return "Attach the evidence file — PDF, PNG or JPG, up to 10 MB.";
    const name = file.name.toLowerCase();
    if (!EVIDENCE_EXTENSIONS.some((ext) => name.endsWith(ext))) {
      return "That file type isn't accepted — evidence must be a PDF, PNG or JPG.";
    }
    if (file.size > EVIDENCE_MAX_BYTES) {
      return `That file is ${formatBytes(file.size)} — evidence is capped at 10 MB.`;
    }
    return null;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (busy) return; // double-submission guard (the button is disabled anyway)

    const validationError = validate();
    if (validationError) {
      setFailedPhase(null);
      setError({ message: validationError });
      return;
    }

    setError(null);
    setFailedPhase(null);
    setPhase(PHASE.UPLOADING);
    let pinned;
    try {
      pinned = await uploadEvidence(file);
    } catch (uploadError) {
      setFailedPhase("upload");
      setError(uploadError);
      setPhase(PHASE.IDLE);
      return;
    }

    setPinnedCid(pinned.cid);
    setPhase(PHASE.PROPOSING);
    try {
      const data = await api("/records/propose", {
        method: "POST",
        body: {
          studentId: Number(studentId),
          field,
          oldValue: oldValue.trim(),
          newValue: newValue.trim(),
          ipfsCid: pinned.cid,
        },
      });
      setResult({ ...data, cid: pinned.cid });
      setPhase(PHASE.SUCCESS);
    } catch (proposeError) {
      setFailedPhase("propose");
      setError(proposeError);
      setPhase(PHASE.IDLE);
    }
  }

  function resetForm() {
    setStudentId("");
    setField(FIELDS[0].value);
    setOldValue("");
    setNewValue("");
    setFile(null);
    setPhase(PHASE.IDLE);
    setFailedPhase(null);
    setError(null);
    setPinnedCid(null);
    setResult(null);
    if (fileInputRef.current) fileInputRef.current.value = ""; // allow re-picking the same file
  }

  if (phase === PHASE.SUCCESS) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <span className="text-3xl leading-none">✅</span>
          <div>
            <h2 className="text-xl font-bold text-success">Proposal Submitted ✅</h2>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">
              Proposal #{result.onchain_proposal_id} submitted — 1 of 2 signatures collected. The
              change executes on-chain once the HOD or Exam Controller co-signs it.
            </p>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className={LABEL_CLASS}>Student</dt>
            <dd className="mt-0.5 font-semibold text-ink">#{result.proposal.studentId}</dd>
          </div>
          <div>
            <dt className={LABEL_CLASS}>Field</dt>
            <dd className="mt-0.5 font-semibold text-ink">{result.proposal.field}</dd>
          </div>
          <div>
            <dt className={LABEL_CLASS}>Change</dt>
            <dd className="mt-0.5 font-semibold text-ink">
              {result.proposal.oldValue ? `${result.proposal.oldValue} → ` : ""}
              {result.proposal.newValue}
            </dd>
          </div>
          <div>
            <dt className={LABEL_CLASS}>Block</dt>
            <dd className="mt-0.5 font-semibold text-ink">#{result.block_number}</dd>
          </div>
        </dl>

        <div className="mt-4">
          <p className={LABEL_CLASS}>Transaction hash</p>
          <p className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
            {result.transaction_hash}
          </p>
        </div>

        <div className="mt-4">
          <p className={LABEL_CLASS}>Evidence (IPFS)</p>
          <p className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
            {result.cid}
          </p>
          <a
            href={ipfsGatewayUrl(result.cid)}
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-block text-sm font-semibold text-primary underline hover:text-primary-strong"
          >
            View evidence on the IPFS gateway ↗
          </a>
        </div>

        {result.mirror_action === "updated" ? (
          <p className="mt-3 text-xs leading-relaxed text-ink-muted">
            Local mirror note: refreshed an existing local row for this on-chain id (a leftover
            from a previous contract deployment — the chain stays the source of truth).
          </p>
        ) : null}

        <div className="mt-5">
          <Button variant="secondary" onClick={resetForm}>
            Propose another change
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card
      title="Propose a record change"
      description="Changes need a second signature: your proposal anchors on-chain with its IPFS evidence, then the HOD or Exam Controller co-signs to execute it."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="propose-student" className={LABEL_CLASS}>
              Student ID
            </label>
            <input
              id="propose-student"
              type="number"
              min="1"
              step="1"
              value={studentId}
              onChange={(event) => setStudentId(event.target.value)}
              disabled={busy}
              placeholder="e.g. 2024001"
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <label htmlFor="propose-field" className={LABEL_CLASS}>
              Field
            </label>
            <select
              id="propose-field"
              value={field}
              onChange={(event) => setField(event.target.value)}
              disabled={busy}
              className={INPUT_CLASS}
            >
              {FIELDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="propose-old" className={LABEL_CLASS}>
              Old value <span className="normal-case text-ink-muted/70">(optional)</span>
            </label>
            <input
              id="propose-old"
              type="text"
              value={oldValue}
              onChange={(event) => setOldValue(event.target.value)}
              disabled={busy}
              placeholder="Current value, e.g. B"
              className={INPUT_CLASS}
            />
          </div>
          <div>
            <label htmlFor="propose-new" className={LABEL_CLASS}>
              New value
            </label>
            <input
              id="propose-new"
              type="text"
              value={newValue}
              onChange={(event) => setNewValue(event.target.value)}
              disabled={busy}
              placeholder="e.g. A"
              className={INPUT_CLASS}
            />
          </div>
        </div>

        <div>
          <label htmlFor="propose-evidence" className={LABEL_CLASS}>
            Evidence
          </label>
          <input
            ref={fileInputRef}
            id="propose-evidence"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            onChange={(event) =>
              setFile(event.target.files && event.target.files[0] ? event.target.files[0] : null)
            }
            disabled={busy}
            className={FILE_INPUT_CLASS}
          />
          <p className="mt-1 text-xs text-ink-muted">
            {file
              ? `${file.name} · ${formatBytes(file.size)}`
              : "Justification document pinned on IPFS — PDF, PNG or JPG, up to 10 MB."}
          </p>
        </div>

        {busy ? (
          <div className="flex items-center gap-3 rounded-lg bg-surface-muted px-4 py-3 text-ink-muted">
            <Spinner className="h-5 w-5" />
            <p className="text-sm leading-relaxed">
              {phase === PHASE.UPLOADING
                ? "Uploading evidence… pinning the file on IPFS."
                : `Evidence pinned${pinnedCid ? ` (${pinnedCid.slice(0, 16)}…)` : ""} — anchoring the proposal on-chain.`}
            </p>
          </div>
        ) : null}

        {error ? (
          <StatusBanner
            tone="error"
            title={
              failedPhase === "upload"
                ? "Evidence upload failed"
                : failedPhase === "propose"
                  ? "Couldn't submit the proposal"
                  : "Check the form"
            }
            detail={error.status ? error.message : undefined}
          >
            {describeRecordsError(error)}
          </StatusBanner>
        ) : null}

        <div>
          <Button type="submit" loading={busy}>
            {phase === PHASE.UPLOADING
              ? "Uploading evidence…"
              : phase === PHASE.PROPOSING
                ? "Submitting proposal…"
                : "Propose Change"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
