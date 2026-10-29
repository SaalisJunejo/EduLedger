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
  statusMeta,
} from "../../lib/workload.js";

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
  const creditedProfessorId = log?.professorId ?? facultyId;
  const conductor = log?.conductedByName || professorLabel(log?.conductedBy);

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
            <> {formatDateTime(log.timestamp)} (Asia/Karachi) · Credited to{" "}
              {professorLabel(creditedProfessorId)}, conducted by {conductor} · tx{" "}
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

        </>
      )}
    </Card>
  );
}
