import { useCallback, useState } from "react";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import LiveSessionCard from "../components/instructor/LiveSessionCard.jsx";
import ProposeChangeCard from "../components/instructor/ProposeChangeCard.jsx";
import { api } from "../lib/api.js";
import { describeApiError } from "../lib/attendanceMessages.js";

// No class-management UI exists yet (Phase 3+); the backend accepts any
// class_id string, so this dropdown is a mock roster for the demo.
const MOCK_CLASSES = [
  { id: "BSCS-401", label: "BSCS-401 · Distributed Systems" },
  { id: "BSCS-402", label: "BSCS-402 · Blockchain Engineering" },
  { id: "BSCS-501", label: "BSCS-501 · Information Security" },
];

export default function InstructorPage() {
  const [classId, setClassId] = useState(MOCK_CLASSES[0].id);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [session, setSession] = useState(null);
  const [endedNote, setEndedNote] = useState(null);

  async function startSession() {
    setStarting(true);
    setStartError(null);
    setEndedNote(null);
    try {
      const data = await api("/session/start", { method: "POST", body: { class_id: classId } });
      setSession({ ...data.session, rotation_seconds: data.rotation_seconds });
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
      description="Start a class session and project the rotating QR code — students verify their identity, scan, and attendance locks on-chain. Below: propose record changes with IPFS evidence, executed once the HOD or Exam Controller co-signs."
    >
      {endedNote ? (
        <StatusBanner tone={endedNote.reason === "closed" ? "warning" : "info"} title="Session ended">
          {endedNote.reason === "closed"
            ? "This session was closed or replaced on the backend — the QR feed stopped. Start a new session to project a fresh code."
            : "Session closed on this projector. The backend has no end-session endpoint yet, so the session stays live server-side until a new one starts for the same class."}
        </StatusBanner>
      ) : null}

      {session ? (
        <LiveSessionCard session={session} onEnded={handleEnded} />
      ) : (
        <Card
          title="Start a session"
          description="Choose the class taking attendance. Starting a session closes any live session of that class first (one live session per class)."
        >
          <div className="flex flex-col gap-3 sm:flex-row">
            <label className="sr-only" htmlFor="class-select">
              Class
            </label>
            <select
              id="class-select"
              value={classId}
              onChange={(event) => setClassId(event.target.value)}
              disabled={starting}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60"
            >
              {MOCK_CLASSES.map((mockClass) => (
                <option key={mockClass.id} value={mockClass.id}>
                  {mockClass.label}
                </option>
              ))}
            </select>
            <Button onClick={startSession} loading={starting} className="sm:w-44">
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

      {/* Module 2 — record-change proposals, clearly separated below the
          attendance flow (which keeps working exactly as before). */}
      <div className="border-t border-line pt-5">
        <h2 className="text-xs font-bold uppercase tracking-wide text-ink-muted">
          Record changes — multi-signature audit trail
        </h2>
        <div className="mt-3">
          <ProposeChangeCard />
        </div>
      </div>
    </PageLayout>
  );
}
