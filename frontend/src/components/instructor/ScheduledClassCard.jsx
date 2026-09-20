import { useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import ZeroAttendanceAction from "./ZeroAttendanceAction.jsx";
import { api } from "../../lib/api.js";
import {
  PROFESSORS,
  PROFESSOR_ID,
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
 * (poll /faculty/logs); while a class has no SessionLog yet, the professor
 * can start its live QR session, declare zero attendance (only once a
 * session they conduct is live), and issue a one-shot 30-minute substitute
 * key for the class. Once the outcome anchors on-chain, the badge replaces
 * the actions — one entry per class, forever.
 */
export default function ScheduledClassCard({
  scheduledClass,
  log,
  liveHere,
  viewerIsConductor,
  onSessionStarted,
  onWorkloadRecorded,
}) {
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [issueOpen, setIssueOpen] = useState(false);
  const [issueSubstituteId, setIssueSubstituteId] = useState(
    PROFESSORS.find((entry) => entry.id !== PROFESSOR_ID)?.id ?? PROFESSOR_ID
  );
  const [issuing, setIssuing] = useState(false);
  const [issueError, setIssueError] = useState(null);
  const [issued, setIssued] = useState(null); // issue response (token + expiry)
  const [copied, setCopied] = useState(false);

  // The badge mirrors the on-chain outcome; while a session runs but
  // hasn't concluded yet, a "live" chip makes the started state obvious
  // (otherwise the card would still read "Awaiting session" right after
  // Start Session).
  const badge = log
    ? statusMeta(log.status)
    : liveHere
      ? viewerIsConductor
        ? { label: "Session live", chip: "bg-primary-soft text-primary-strong" }
        : { label: "Live · substitute covering", chip: "bg-primary-soft text-primary-strong" }
      : { label: "Awaiting session", chip: "bg-surface-muted text-ink-muted" };
  // The workload credits whoever conducted the class — when that isn't the
  // scheduled professor, a substitute covered it.
  const substituteCovered = Boolean(log?.class && log.conductedBy !== log.class.professorId);

  async function startSession() {
    setStarting(true);
    setStartError(null);
    try {
      const data = await api("/faculty/session/start", {
        method: "POST",
        body: { professor_id: PROFESSOR_ID, class_id: scheduledClass.id },
      });
      onSessionStarted(data, scheduledClass);
    } catch (error) {
      setStartError(error);
    } finally {
      setStarting(false);
    }
  }

  async function issueKey() {
    setIssuing(true);
    setIssueError(null);
    try {
      const data = await api("/faculty/substitute/issue", {
        method: "POST",
        body: {
          professor_id: PROFESSOR_ID,
          class_id: scheduledClass.id,
          substitute_id: Number(issueSubstituteId),
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
            {scheduledClass.courseId} · {scheduledClass.title}
          </h3>
          <p className="mt-1 text-xs text-ink-muted">
            Room {scheduledClass.room} · starts {scheduledClass.start} today ·{" "}
            {professorLabel(scheduledClass.professorId)}
          </p>
        </div>
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold ${badge.chip}`}
        >
          {badge.label}
        </span>
      </div>

      {log ? (
        <p className="mt-3 text-xs leading-relaxed text-ink-muted">
          Anchored on-chain {formatDateTime(log.timestamp)} · workload credited to{" "}
          {professorLabel(log.conductedBy)}
          {substituteCovered ? " (substitute)" : ""} · tx{" "}
          <span className="font-mono">{shortHash(log.onchainTxHash)}</span>
        </p>
      ) : (
        <>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-start">
            <Button onClick={startSession} loading={starting}>
              {liveHere ? "Restart Session" : "Start Session"}
            </Button>

            <ZeroAttendanceAction
              classId={scheduledClass.id}
              conductorId={PROFESSOR_ID}
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
                Substitute professor
              </label>
              <div className="mt-1.5 flex flex-col gap-3 sm:flex-row">
                <select
                  id={`substitute-select-${scheduledClass.id}`}
                  value={issueSubstituteId}
                  onChange={(event) => setIssueSubstituteId(event.target.value)}
                  disabled={issuing}
                  className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60 sm:flex-1"
                >
                  {PROFESSORS.filter((entry) => entry.id !== PROFESSOR_ID).map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {professorLabel(entry.id)}
                    </option>
                  ))}
                </select>
                <Button onClick={issueKey} loading={issuing} className="sm:w-32">
                  Issue Key
                </Button>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
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
                Valid until {formatDateTime(issued.expires_at)} ({issued.expires_in_minutes}{" "}
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
