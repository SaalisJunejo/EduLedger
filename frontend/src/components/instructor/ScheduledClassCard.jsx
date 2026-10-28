import { useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import ZeroAttendanceAction from "./ZeroAttendanceAction.jsx";
import { api } from "../../lib/api.js";
import {
  describeWorkloadError,
  formatDateTime,
  professorLabel,
  shortHash,
  shortToken,
  statusMeta,
} from "../../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

/**
 * One scheduled class of the professor's day (Module 3 — My Classes).
 *
 * States, top to bottom: the workload badge mirrors the on-chain outcome
 * (poll /faculty/classes); while a class has no SessionLog yet, the professor
 * can start its live QR session, declare zero attendance (only once a
 * session they conduct is live), and issue a one-shot 30-minute substitute
 * key for the class. Once the outcome anchors on-chain, the badge replaces
 * the actions — one entry per class, forever.
 */
export default function ScheduledClassCard({
  scheduledClass,
  facultyId,
  log,
  liveHere,
  viewerIsConductor,
  onSessionStarted,
  onWorkloadRecorded,
}) {
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [issueOpen, setIssueOpen] = useState(false);
  const [issueSubstituteId, setIssueSubstituteId] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [issueError, setIssueError] = useState(null);
  const [issued, setIssued] = useState(null); // issue response (token + expiry)
  const [copied, setCopied] = useState(false);

  // The badge mirrors the on-chain outcome; while a session runs but
  // hasn't concluded yet, a "live" chip makes the started state obvious
  // (otherwise the card would still read "Awaiting session" right after
  // Start Session).
  const outcome = log?.status || scheduledClass.outcome;
  const badge = outcome
    ? statusMeta(outcome)
    : liveHere
      ? viewerIsConductor
        ? { label: "Session live", chip: "bg-primary-soft text-primary-strong" }
        : { label: "Live · substitute covering", chip: "bg-primary-soft text-primary-strong" }
      : { label: "Awaiting session", chip: "bg-surface-muted text-ink-muted" };
  // The workload credits whoever conducted the class — when that isn't the
  // scheduled professor, a substitute covered it.
  const substituteCovered = Boolean(log && log.conductedBy !== facultyId);

  async function startSession() {
    setStarting(true);
    setStartError(null);
    try {
      const data = await api("/faculty/session/start", {
        method: "POST",
        body: { class_id: scheduledClass.id },
      });
      onSessionStarted(data, scheduledClass);
    } catch (error) {
      setStartError(error);
    } finally {
      setStarting(false);
    }
  }

  async function issueKey() {
    const substituteId = Number(issueSubstituteId);
    if (!Number.isSafeInteger(substituteId) || substituteId <= 0 || substituteId === facultyId) {
      setIssueError({ message: "Enter another faculty member's real user ID (a positive integer)." });
      return;
    }
    setIssuing(true);
    setIssueError(null);
    try {
      const data = await api("/faculty/substitute/issue", {
        method: "POST",
        body: {
          class_id: scheduledClass.id,
          substitute_id: substituteId,
        },
      });
      setIssued(data);
      setCopied(false);
    } catch (error) {
      setIssueError(error);
    } finally {
      setIssuing(false);
    }
  }

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(issued.substitute_token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the full key stays selectable in its block */
    }
  }

  function toggleIssueForm() {
    setIssueOpen((open) => !open);
    setIssued(null);
    setIssueError(null);
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-ink">
            {scheduledClass.courseCode}
          </h3>
          <p className="mt-1 text-xs text-ink-muted">
            Room {scheduledClass.room} · starts {formatDateTime(scheduledClass.startTime)} (Asia/Karachi) ·{" "}
            {professorLabel(facultyId)}
          </p>
        </div>
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold ${badge.chip}`}
        >
          {badge.label}
        </span>
      </div>

      {outcome ? (
        <p className="mt-3 text-xs leading-relaxed text-ink-muted">
          Final outcome recorded on the workload ledger.
          {log ? (
            <> {formatDateTime(log.timestamp)} (Asia/Karachi) · workload credited to{" "}
              {professorLabel(log.conductedBy)}{substituteCovered ? " (substitute)" : ""} · tx{" "}
              <span className="font-mono">{shortHash(log.onchainTxHash)}</span>
            </>
          ) : null}
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start">
            <Button onClick={startSession} loading={starting}>
              {liveHere ? "Restart Session" : "Start Session"}
            </Button>

            <ZeroAttendanceAction
              classId={scheduledClass.id}
              disabled={!liveHere || !viewerIsConductor}
              disabledHint={
                !liveHere
                  ? "Zero attendance needs a started session."
                  : !viewerIsConductor
                    ? "A substitute is conducting this class — they declare zero attendance from their live view."
                    : ""
              }
              onDeclared={onWorkloadRecorded}
            />

            <Button variant="secondary" onClick={toggleIssueForm}>
              {issueOpen ? "Hide Substitute Key" : "Issue Substitute Key"}
            </Button>
          </div>

          {startError ? (
            <div className="mt-4">
              <StatusBanner
                tone="error"
                title="Couldn't start the session"
                detail={startError.status ? startError.message : undefined}
              >
                {describeWorkloadError(startError)}
              </StatusBanner>
            </div>
          ) : null}

          {issueOpen && !issued ? (
            <div className="mt-4 rounded-lg border border-line bg-surface-muted p-4">
              <label
                htmlFor={`substitute-select-${scheduledClass.id}`}
                className={LABEL_CLASS}
              >
                Substitute faculty user ID
              </label>
              <div className="mt-1.5 flex flex-col gap-3 sm:flex-row">
                <input
                  id={`substitute-select-${scheduledClass.id}`}
                  type="number"
                  min="1"
                  step="1"
                  placeholder="Colleague's faculty user ID"
                  value={issueSubstituteId}
                  onChange={(event) => setIssueSubstituteId(event.target.value)}
                  disabled={issuing}
                  className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60 sm:flex-1"
                />
                <Button onClick={issueKey} loading={issuing} className="sm:w-32">
                  Issue Key
                </Button>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                No faculty directory endpoint is available: enter the colleague's actual user ID.
                The key is a signed token valid for 30 minutes and redeemable exactly once —
                only by the selected professor. Redeeming starts the class session with the
                workload credited to them as substitute.
              </p>

              {issueError ? (
                <div className="mt-3">
                  <StatusBanner
                    tone="error"
                    title="Couldn't issue the substitute key"
                    detail={issueError.status ? issueError.message : undefined}
                  >
                    {describeWorkloadError(issueError)}
                  </StatusBanner>
                </div>
              ) : null}
            </div>
          ) : null}

          {issued ? (
            <div className="mt-4 rounded-lg border border-success/30 bg-success-soft p-4">
              <p className="text-sm font-semibold text-success">
                Substitute key issued — {professorLabel(issued.substitute_id)}
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-success">
                Valid until {formatDateTime(issued.expires_at)} (Asia/Karachi; {issued.expires_in_minutes}{" "}
                minutes). Give the code below to your substitute — they redeem it from their own
                dashboard&apos;s &quot;Redeem Substitute Key&quot;.
              </p>
              <p className="mt-2 text-center font-mono text-sm font-bold text-ink">
                {shortToken(issued.substitute_token)}
              </p>
              <p className="mt-2 break-all rounded-lg bg-surface px-3 py-2 font-mono text-xs text-ink">
                {issued.substitute_token}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button variant="secondary" onClick={copyToken}>
                  {copied ? "Copied ✓" : "Copy full key"}
                </Button>
                <Button variant="secondary" onClick={toggleIssueForm}>
                  Done
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
