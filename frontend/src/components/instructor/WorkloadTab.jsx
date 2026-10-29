import { useCallback, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import LiveSessionCard from "./LiveSessionCard.jsx";
import RedeemSubstituteCard from "./RedeemSubstituteCard.jsx";
import ScheduledClassCard from "./ScheduledClassCard.jsx";
import ZeroAttendanceAction from "./ZeroAttendanceAction.jsx";
import {
  formatDateTime,
  professorLabel,
  statusMeta,
} from "../../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

/**
 * The "My Classes · Workload" tab of the instructor dashboard (Module 3).
 *
 * Layout, top to bottom: the live projector view for whichever class was
 * started (the SAME LiveSessionCard as the attendance tab — faculty QR
 * sessions are plain Module 1 sessions under the hood, so its 2-second QR
 * polling, countdown bar and LIVE chip apply unchanged), the professor's
 * class list for today, and the substitute-key redemption (the surface a
 * covering colleague uses as themselves).
 *
 * The page polls /faculty/classes to keep owned-class badges current: when a
 * class's outcome anchors on-chain — a student scan, a zero-attendance
 * declaration, a substitute concluding a covered class, or the background
 * auto-flag — the live view swaps to the outcome card and the class badge
 * updates, no manual refresh needed.
 */
export default function WorkloadTab({ user, classes, onRefresh }) {
  // Full log details are available only from actions performed here, not
  // from /faculty/classes. Never call the HOD/ADMIN-only /faculty/logs.
  const [recordedLogs, setRecordedLogs] = useState(new Map());
  const [liveSession, setLiveSession] = useState(null);
  const [endedNote, setEndedNote] = useState(null); // { reason, message }

  function handleRecorded(data) {
    if (data.session_log) {
      setRecordedLogs((previous) => new Map(previous).set(data.session_log.classId, data.session_log));
    }
    onRefresh();
  }

  const liveLog = liveSession ? recordedLogs.get(liveSession.classId) : null;
  const liveOutcome = liveSession
    ? liveLog?.status || classes?.find((entry) => entry.id === liveSession.classId)?.outcome
    : null;

  function handleSessionStarted(data, scheduledClass) {
    const label = `${scheduledClass.courseCode} · Room ${scheduledClass.room}`;
    setEndedNote(null);
    setLiveSession({
      classId: scheduledClass.id,
      classLabel: label,
      scheduledProfessorId: user.id,
      conductorId: data.workload_session.conductorId,
      substitute: data.workload_session.conductorId !== user.id,
      // LiveSessionCard renders class_id as its title — show the course
      // instead of the internal "workload-<id>" session namespace.
      session: { ...data.session, class_id: label, rotation_seconds: data.rotation_seconds },
    });
  }

  function handleRedeemed(data) {
    // The redeem response only supplies the covered class ID. The owned
    // class list cannot resolve another professor's course or room.
    const label = `Scheduled class #${data.class_id}`;
    setEndedNote(null);
    setLiveSession({
      classId: data.class_id,
      classLabel: label,
      scheduledProfessorId: null,
      conductorId: data.workload_session.conductorId,
      substitute: true,
      session: { ...data.session, class_id: label, rotation_seconds: data.rotation_seconds },
    });
    onRefresh();
  }

  const handleEnded = useCallback((reason, error) => {
    setLiveSession(null);
    setEndedNote({ reason, message: error ? error.message : null });
  }, []);

  return (
    <div className="flex flex-col gap-5">
      <RedeemSubstituteCard user={user} classes={classes} onRedeemed={handleRedeemed} />

      {endedNote ? (
        <StatusBanner tone={endedNote.reason === "closed" ? "warning" : "info"} title="Session ended">
          {endedNote.reason === "closed"
            ? "This session was closed or replaced on the backend — the QR feed stopped. A substitute redeeming a key for this class replaces the live session; start a new one to project a fresh code."
            : "The attendance session is closed on the backend — no more check-ins. The class itself still needs its outcome: a student scan already locked counts, otherwise declare zero attendance or start a new session for the same class."}
        </StatusBanner>
      ) : null}

      {liveSession ? (
        liveOutcome ? (
          <ConductedOutcomeCard
            classLabel={liveSession.classLabel}
            scheduledProfessorId={liveSession.scheduledProfessorId}
            outcome={liveOutcome}
            log={liveLog}
            onBack={() => setLiveSession(null)}
          />
        ) : (
          <>
            {liveSession.substitute ? (
              <StatusBanner tone="info" title="Covering as substitute">
                You&apos;re conducting {liveSession.classLabel} as the substitute (
                {professorLabel(liveSession.conductorId)}) for{" "}
                {liveSession.scheduledProfessorId
                  ? professorLabel(liveSession.scheduledProfessorId)
                  : "the scheduled professor"}
                . Workload is credited to the primary professor; you are recorded as conductor.
                Course and room details are not included in the current redeem response.
              </StatusBanner>
            ) : null}

            <LiveSessionCard key={liveSession.session.id} session={liveSession.session} onEnded={handleEnded} />

            {liveSession.substitute ? (
              <Card
                title="Conclude the covered class"
                description="A student scan records the workload outcome. Keep taking attendance until you end the session. If nobody scans, declare zero attendance as the covering substitute — credit stays with the primary professor."
              >
                <ZeroAttendanceAction
                  classId={liveSession.classId}
                  onDeclared={handleRecorded}
                />
                <p className="mt-3 text-xs text-ink-muted">
                  The class list only reports your own scheduled classes. A covered colleague's
                  outcome cannot be refreshed here after a student scan; zero-attendance results
                  are shown when declared from this view.
                </p>
              </Card>
            ) : null}
          </>
        )
      ) : null}

      <section>
        <h2 className={LABEL_CLASS}>My scheduled classes · Asia/Karachi</h2>
        {classes?.length === 0 ? (
          <p className="mt-3 text-sm text-ink-muted">No classes assigned yet. Ask your HOD to upload your timetable.</p>
        ) : null}
        <div className="mt-3 flex flex-col gap-4">
          {(classes || []).map((scheduledClass) => (
            <ScheduledClassCard
              key={scheduledClass.id}
              scheduledClass={scheduledClass}
              facultyId={user.id}
              log={recordedLogs.get(scheduledClass.id)}
              liveHere={liveSession?.classId === scheduledClass.id && !liveOutcome}
              viewerIsConductor={
                liveSession?.classId === scheduledClass.id &&
                liveSession?.conductorId === user.id
              }
              onSessionStarted={handleSessionStarted}
              onWorkloadRecorded={handleRecorded}
            />
          ))}
        </div>
      </section>

    </div>
  );
}

/**
 * The class's final outcome, shown in place of the projector view once the
 * workload entry anchors on-chain (student scan, zero-attendance
 * declaration or auto-flag). Mirrors the audit-trail module's success card:
 * heading, dl grid and the on-chain transaction hash in a mono block.
 */
function ConductedOutcomeCard({ classLabel, scheduledProfessorId, outcome, log, onBack }) {
  const meta = statusMeta(outcome);
  const creditedProfessorId = log?.professorId ?? scheduledProfessorId;
  const conductor = log?.conductedByName || professorLabel(log?.conductedBy);

  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="text-3xl leading-none">{outcome === "UNCONDUCTED" ? "⚠️" : "✅"}</span>
        <div>
          <h3 className="text-xl font-bold text-ink">{classLabel}</h3>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            {meta.label} — anchored on the workload ledger.
            {log ? (
              <> Credited to <span className="font-semibold text-ink">{professorLabel(creditedProfessorId)}</span>,
                {" "}conducted by <span className="font-semibold text-ink">{conductor}</span>.</>
            ) : null}
          </p>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className={LABEL_CLASS}>Status</dt>
          <dd className="mt-0.5 font-semibold text-ink">{meta.label}</dd>
        </div>
        {log ? (
          <div>
            <dt className={LABEL_CLASS}>Recorded · Asia/Karachi</dt>
            <dd className="mt-0.5 font-semibold text-ink">{formatDateTime(log.timestamp)}</dd>
          </div>
        ) : null}
      </dl>

      {log ? (
        <div className="mt-4">
          <p className={LABEL_CLASS}>Transaction hash</p>
          <p className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
            {log.onchainTxHash}
          </p>
        </div>
      ) : null}

      <div className="mt-5">
        <Button variant="secondary" onClick={onBack}>
          Back to My Classes
        </Button>
      </div>
    </Card>
  );
}
