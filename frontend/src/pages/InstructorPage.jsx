import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import LiveSessionCard from "../components/instructor/LiveSessionCard.jsx";
import ExportReportCard from "../components/ExportReportCard.jsx";
import GradeCorrectionRequestCard from "../components/instructor/GradeCorrectionRequestCard.jsx";
import WorkloadTab from "../components/instructor/WorkloadTab.jsx";
import { api } from "../lib/api.js";
import { describeApiError } from "../lib/attendanceMessages.js";

import { describeWorkloadError, formatDateTime, loadFacultyClasses } from "../lib/workload.js";

const CLASSES_POLL_MS = 5000;

// Dashboard sections as tabs on this route (no new routes — see App.jsx),
// the same pattern as the HOD dashboard. Unlike HodPage, sections stay
// mounted and are hidden with CSS instead of unmounted, so a live
// attendance QR keeps polling while the instructor checks another tab
// mid-session.
const TABS = [
  { id: "attendance", label: "Attendance Session" },
  { id: "workload", label: "My Classes · Workload" },
  { id: "grades", label: "Grade corrections" },
];

export default function InstructorPage() {
  const user = useOutletContext();
  const [tab, setTab] = useState("attendance");
  const [classes, setClasses] = useState(null);
  const [classesError, setClassesError] = useState(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [classId, setClassId] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [session, setSession] = useState(null);
  const [endedNote, setEndedNote] = useState(null);

  const refreshClasses = useCallback(() => setRefreshVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    let inFlight = false;
    async function poll() {
      if (inFlight) return;
      inFlight = true;
      try {
        const rows = await loadFacultyClasses();
        if (active) {
          setClasses(rows);
          setClassesError(null);
        }
      } catch (error) {
        if (active) setClassesError(error);
      } finally {
        inFlight = false;
      }
    }
    poll();
    const timer = setInterval(poll, CLASSES_POLL_MS);
    return () => { active = false; clearInterval(timer); };
  }, [user.id, refreshVersion]);

  const selectedClass = classes?.find((entry) => String(entry.id) === classId) || classes?.[0];

  async function startSession() {
    if (starting || !selectedClass) return;
    setStarting(true);
    setStartError(null);
    setEndedNote(null);
    try {
      const data = await api("/session/start", {
        method: "POST", body: { class_id: selectedClass.id },
      });
      setSession({
        ...data.session,
        class_id: `${selectedClass.courseCode} · Room ${selectedClass.room}`,
        rotation_seconds: data.rotation_seconds,
      });
    } catch (error) {
      setStartError(error);
    } finally {
      setStarting(false);
    }
  }

  const handleEnded = useCallback((reason, error) => {
    setSession(null);
    setEndedNote({ reason, message: error ? error.message : null });
  }, []);

  return (
    <PageLayout
      role="Instructor"
      title="Instructor Dashboard"
      wide
      description="Manage live attendance and faculty workload, and request grade corrections with IPFS justification evidence. Only the HOD can approve or reject a grade correction."
    >
      <div role="tablist" aria-label="Instructor dashboard sections" className="flex flex-wrap gap-2">
        {TABS.map((entry) => (
          <Button
            key={entry.id}
            variant={tab === entry.id ? "primary" : "secondary"}
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      {classes === null && !classesError ? (
        <p className="text-sm text-ink-muted" role="status">Loading your scheduled classes…</p>
      ) : null}
      {classesError ? (
        <StatusBanner tone={classes === null ? "error" : "warning"} title="Couldn't refresh your classes">
          {describeWorkloadError(classesError)}
          {classes !== null ? " Showing the last loaded classes; retrying automatically." : null}
        </StatusBanner>
      ) : null}

      <div role="tabpanel" hidden={tab !== "attendance"}>
        {endedNote ? (
          <StatusBanner tone={endedNote.reason === "closed" ? "warning" : "info"} title="Session ended">
            {endedNote.reason === "closed"
              ? "This session was closed or replaced on the backend — the QR feed stopped. Start a new session to project a fresh code."
              : "This session is closed on the backend — rotation stopped and later scans are rejected. Attendance already locked on-chain is unaffected. Start a new session to project a fresh code."}
          </StatusBanner>
        ) : null}

        {session ? (
          <LiveSessionCard key={session.id} session={session} onEnded={handleEnded} />
        ) : (
          <Card
            title="Start a session"
            description="Choose one of your scheduled occurrences. Times are shown in Asia/Karachi. Starting replaces the live attendance session for that occurrence."
          >
            <div className="flex flex-col gap-3 sm:flex-row">
              <label className="sr-only" htmlFor="class-select">
                Class
              </label>
              <select
                id="class-select"
                value={selectedClass?.id ?? ""}
                onChange={(event) => setClassId(event.target.value)}
                disabled={starting || !classes?.length}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60"
              >
                {!classes?.length ? <option value="">No scheduled classes available</option> : null}
                {(classes || []).map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.courseCode} · Room {entry.room} · {formatDateTime(entry.startTime)}
                    {entry.outcome ? ` · ${entry.outcome}` : ""}
                  </option>
                ))}
              </select>
              <Button onClick={startSession} loading={starting} disabled={!selectedClass} className="sm:w-44">
                Start Session
              </Button>
            </div>

            {startError ? (
              <div className="mt-4">
                <StatusBanner tone="error" title="Couldn't start the session">
                  {describeApiError(startError)}
                </StatusBanner>
              </div>
            ) : null}
          </Card>
        )}

        <div className="mt-6">
          <ExportReportCard classes={classes} />
        </div>
      </div>

      {/* Module 3 — faculty workload ledger: today's classes, live QR
          sessions, substitute keys, zero-attendance declarations. */}
      <div role="tabpanel" hidden={tab !== "workload"}>
        <WorkloadTab user={user} classes={classes} onRefresh={refreshClasses} />
      </div>

      {/* Grade corrections and this instructor's requests. */}
      <div role="tabpanel" hidden={tab !== "grades"}>
        <GradeCorrectionRequestCard />
      </div>
    </PageLayout>
  );
}
