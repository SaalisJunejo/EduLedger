import { useCallback, useEffect, useMemo, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import LiveSessionCard from "./LiveSessionCard.jsx";
import RedeemSubstituteCard from "./RedeemSubstituteCard.jsx";
import ScheduledClassCard from "./ScheduledClassCard.jsx";
import ZeroAttendanceAction from "./ZeroAttendanceAction.jsx";
import { api } from "../../lib/api.js";
import {
  MOCK_CLASSES,
  PROFESSOR_ID,
  describeWorkloadError,
  formatDateTime,
  professorLabel,
  statusMeta,
} from "../../lib/workload.js";

// How often the tab re-reads GET /faculty/logs. Badges and the live view's
// "conducted" swap react to student scans / auto-flags within this window.
const LOGS_POLL_MS = 5000;

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
 * A light poll of /faculty/logs keeps every badge current: the moment a
 * class's outcome anchors on-chain — a student scan, a zero-attendance
 * declaration, a substitute concluding a covered class, or the background
 * auto-flag — the live view swaps to the outcome card and the class badge
 * updates, no manual refresh needed.
 */
export default function WorkloadTab() {
  const [logs, setLogs] = useState(null); // null until the first successful load
  const [logsError, setLogsError] = useState(null);
  const [liveSession, setLiveSession] = useState(null);
  const [endedNote, setEndedNote] = useState(null); // { reason, message }

  const fetchLogs = useCallback(async () => {
    try {
      const data = await api("/faculty/logs");
      setLogs(data.logs || []);
      setLogsError(null);
    } catch (error) {
      setLogsError(error);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    const timer = setInterval(fetchLogs, LOGS_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchLogs]);

  // Newest-first list -> one entry per class (the latest, should the
  // backend ever return more than one).
  const logsByClass = useMemo(() => {
    const map = new Map();
    for (const entry of logs || []) {
      if (!map.has(entry.classId)) map.set(entry.classId, entry);
    }
    return map;
  }, [logs]);

  const liveLog = liveSession ? logsByClass.get(liveSession.classId) ?? null : null;

  function handleSessionStarted(data, scheduledClass) {
    const label = `${scheduledClass.courseId} · ${scheduledClass.title}`;
    setEndedNote(null);
    setLiveSession({
      classId: scheduledClass.id,
      classLabel: label,
      scheduledProfessorId: scheduledClass.professorId,
      conductorId: data.workload_session.conductorId,
      substitute: data.workload_session.conductorId !== scheduledClass.professorId,
      // LiveSessionCard renders class_id as its title — show the course
      // instead of the internal "workload-<id>" session namespace.
      session: { ...data.session, class_id: label, rotation_seconds: data.rotation_seconds },
    });
  }

  function handleRedeemed(data) {
    const scheduledClass = MOCK_CLASSES.find((entry) => entry.id === data.class_id);
    const label = scheduledClass
      ? `${scheduledClass.courseId} · ${scheduledClass.title}`
      : `Class #${data.class_id}`;
    setEndedNote(null);
    setLiveSession({
      classId: data.class_id,
      classLabel: label,
      scheduledProfessorId: scheduledClass ? scheduledClass.professorId : null,
      conductorId: data.substitute_id,
      substitute: true,
      session: { ...data.session, class_id: label, rotation_seconds: data.rotation_seconds },
    });
    fetchLogs();
  }

  const handleEnded = useCallback((reason, error) => {
    setLiveSession(null);
    setEndedNote({ reason, message: error ? error.message : null });
  }, []);

  return (
    <div className="flex flex-col gap-5">
      {endedNote ? (
        <StatusBanner tone={endedNote.reason === "closed" ? "warning" : "info"} title="Session ended">
          {endedNote.reason === "closed"
            ? "This session was closed or replaced on the backend — the QR feed stopped. A substitute redeeming a key for this class replaces the live session; start a new one to project a fresh code."
            : "Session closed on this projector. The class stays live server-side — a student scan or zero-attendance declaration can still conclude it, or start a new session for the same class."}
        </StatusBanner>
      ) : null}

      {liveSession ? (
        liveLog ? (
          <ConductedOutcomeCard
            classLabel={liveSession.classLabel}
            scheduledProfessorId={liveSession.scheduledProfessorId}
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
                . When the class concludes, the workload entry credits you — not the scheduled
                professor.
              </StatusBanner>
            ) : null}

            <LiveSessionCard session={liveSession.session} onEnded={handleEnded} />

            {liveSession.substitute ? (
              <Card
                title="Conclude the covered class"
                description="A student scan of the projected QR concludes this class automatically. If nobody scans, declare zero attendance as the covering substitute — the on-chain entry still credits you."
              >
                <ZeroAttendanceAction
                  classId={liveSession.classId}
                  conductorId={liveSession.conductorId}
                  onDeclared={fetchLogs}
                />
              </Card>
            ) : null}
          </>
        )
      ) : null}

      {logsError ? (
        logs === null ? (
          <StatusBanner
            tone="error"
            title="Couldn't load the workload ledger"
            detail={logsError.status ? logsError.message : undefined}
          >
            {describeWorkloadError(logsError)}
          </StatusBanner>
        ) : (
          <StatusBanner tone="warning" title="Lost contact with the workload ledger">
            {describeWorkloadError(logsError)} — showing the last known statuses and retrying
            automatically.
          </StatusBanner>
        )
      ) : null}

      <section>
        <h2 className={LABEL_CLASS}>My classes — today</h2>
        <div className="mt-3 flex flex-col gap-4">
          {MOCK_CLASSES.map((scheduledClass) => (
            <ScheduledClassCard
              key={scheduledClass.id}
              scheduledClass={scheduledClass}
              log={logsByClass.get(scheduledClass.id) ?? null}
              liveHere={liveSession?.classId === scheduledClass.id && !liveLog}
              viewerIsConductor={
                liveSession?.classId === scheduledClass.id &&
                liveSession?.conductorId === PROFESSOR_ID
              }
              onSessionStarted={handleSessionStarted}
              onWorkloadRecorded={fetchLogs}
            />
          ))}
        </div>
      </section>

      <RedeemSubstituteCard onRedeemed={handleRedeemed} />
    </div>
  );
}

/**
 * The class's final outcome, shown in place of the projector view once the
 * workload entry anchors on-chain (student scan, zero-attendance
 * declaration or auto-flag). Mirrors the audit-trail module's success card:
 * heading, dl grid and the on-chain transaction hash in a mono block.
 */
function ConductedOutcomeCard({ classLabel, scheduledProfessorId, log, onBack }) {
  const meta = statusMeta(log.status);
  const substituteCovered =
    Boolean(scheduledProfessorId) && log.conductedBy !== scheduledProfessorId;

  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="text-3xl leading-none">{log.status === "UNCONDUCTED" ? "⚠️" : "✅"}</span>
        <div>
          <h3 className="text-xl font-bold text-ink">{classLabel}</h3>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            {meta.label} — anchored on the workload ledger. Workload credited to{" "}
            <span className="font-semibold text-ink">{professorLabel(log.conductedBy)}</span>
            {substituteCovered ? " (substitute)" : ""}.
          </p>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className={LABEL_CLASS}>Status</dt>
          <dd className="mt-0.5 font-semibold text-ink">{meta.label}</dd>
        </div>
        <div>
          <dt className={LABEL_CLASS}>Recorded</dt>
          <dd className="mt-0.5 font-semibold text-ink">{formatDateTime(log.timestamp)}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <p className={LABEL_CLASS}>Transaction hash</p>
        <p className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
          {log.onchainTxHash}
        </p>
      </div>

      <div className="mt-5">
        <Button variant="secondary" onClick={onBack}>
          Back to My Classes
        </Button>
      </div>
    </Card>
  );
}
