import { useEffect, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { api } from "../../lib/api.js";
import { describeWorkloadError, formatDateTime, loadFacultyColleagues, professorLabel } from "../../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60";

/**
 * "Redeem a substitute key" — the other half of the substitute flow.
 *
 * This represents a colleague logging in as themselves on their own
 * dashboard: they paste the key a professor issued to their authenticated
 * account. Redeeming starts the class's live session with the substitute as
 * conductor, while the primary professor keeps the workload credit. The
 * parent renders the same live screen used for a normal session.
 */
export default function RedeemSubstituteCard({ user, classes, onRedeemed }) {
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null); // ApiError or { message } for validation
  const [redeemed, setRedeemed] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;

    if (!token.trim()) {
      setError({ message: "Paste the substitute key first — the full token the professor issued." });
      return;
    }

    setSubmitting(true);
    setError(null);
    setRedeemed(null);
    try {
      const data = await api("/faculty/substitute/redeem", {
        method: "POST",
        body: { token: token.trim() },
      });
      setToken("");
      setRedeemed(data);
      onRedeemed(data);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      title="Substitute"
      description="Delegate one of your classes or cover a colleague. Workload is credited to the primary professor; the substitute is recorded as conductor."
    >
      <IssueSubstituteForm user={user} classes={classes} />
      <h3 className="mb-4 mt-6 border-t border-line pt-5 text-sm font-bold text-ink">I'm covering a class</h3>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className={LABEL_CLASS}>Redeeming as</p>
            <p className="mt-1.5 rounded-lg border border-line bg-surface-muted px-3 py-2.5 text-sm text-ink">
              {user.fullName || user.email} · {professorLabel(user.id)}
            </p>
            <p className="mt-1 text-xs text-ink-muted">
              Only a key issued for your logged-in account can be redeemed.
            </p>
          </div>
          <div>
            <label htmlFor="redeem-token" className={LABEL_CLASS}>
              Substitute key
            </label>
            <input
              id="redeem-token"
              type="text"
              value={token}
              onChange={(event) => { setToken(event.target.value); setError(null); setRedeemed(null); }}
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              placeholder="Paste the full key (eyJhbGciOiJIUzI1NiIs…)"
              className={`${INPUT_CLASS} font-mono`}
            />
            <p className="mt-1 text-xs text-ink-muted">
              Keys expire 30 minutes after issue and can be redeemed once.
            </p>
          </div>
        </div>

        {error ? (
          <StatusBanner
            tone="error"
            title="Couldn't redeem the key"
            detail={error.status ? error.message : undefined}
          >
            {describeWorkloadError(error)}
          </StatusBanner>
        ) : null}

        <div>
          <Button type="submit" loading={submitting}>
            Redeem Key
          </Button>
        </div>
      </form>
      {redeemed ? (
        <div className="mt-4" role="status">
          <StatusBanner tone="success" title="Key redeemed — session started">
            Started a session for {redeemed.courseCode || `scheduled class #${redeemed.class_id}`}
            {redeemed.room ? ` · Room: ${redeemed.room}` : ""}
            {redeemed.primaryProfessorName ? ` · Primary professor: ${redeemed.primaryProfessorName}` : ""}.
            {" "}The live QR, numeric code, attendance panel and End Session button appear below
            while the session is active. Workload credit stays with the primary professor.
          </StatusBanner>
        </div>
      ) : null}
    </Card>
  );
}

function IssueSubstituteForm({ user, classes }) {
  const [colleagues, setColleagues] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [classId, setClassId] = useState("");
  const [colleagueId, setColleagueId] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [error, setError] = useState(null);
  const [issued, setIssued] = useState(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  useEffect(() => {
    let active = true;
    setColleagues(null);
    setLoadError(null);
    loadFacultyColleagues()
      .then((rows) => { if (active) setColleagues(rows.filter((entry) => entry.id !== user.id)); })
      .catch((loadFailure) => { if (active) setLoadError(loadFailure); });
    return () => { active = false; };
  }, [user.id, refreshVersion]);

  const availableClasses = (classes || []).filter((entry) => !entry.outcome);
  const selectedClass = availableClasses.find((entry) => String(entry.id) === classId);
  const selectedColleague = colleagues?.find((entry) => String(entry.id) === colleagueId);

  async function issue(event) {
    event.preventDefault();
    if (issuing) return;
    if (!selectedClass || !selectedColleague) {
      setError({ message: "Pick one of your classes and a colleague from the lists." });
      return;
    }
    setIssuing(true);
    setError(null);
    setIssued(null);
    setCopied(false);
    setCopyError(false);
    try {
      const data = await api("/faculty/substitute/issue", {
        method: "POST",
        body: { class_id: selectedClass.id, substitute_id: selectedColleague.id },
      });
      setIssued({
        ...data,
        classLabel: `${selectedClass.courseCode} · Room ${selectedClass.room}`,
        colleagueName: selectedColleague.full_name || professorLabel(selectedColleague.id),
      });
    } catch (issueError) {
      setError(issueError);
    } finally {
      setIssuing(false);
    }
  }

  function selectionChanged() {
    setIssued(null);
    setError(null);
    setCopied(false);
    setCopyError(false);
  }

  async function copyKey() {
    try {
      await navigator.clipboard.writeText(issued.substitute_token);
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  }

  return (
    <section>
      <h3 className="text-sm font-bold text-ink">I can't take this class</h3>
      <form onSubmit={issue} className="mt-4 flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="substitute-class" className={LABEL_CLASS}>My class</label>
            <select
              id="substitute-class"
              value={selectedClass ? classId : ""}
              onChange={(event) => { setClassId(event.target.value); selectionChanged(); }}
              disabled={issuing || !availableClasses.length}
              className={INPUT_CLASS}
            >
              <option value="">{classes === null ? "Loading your classes…" : "Choose a class"}</option>
              {availableClasses.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.courseCode} · Room {entry.room} · {formatDateTime(entry.startTime)}
                </option>
              ))}
            </select>
            {classes !== null && !availableClasses.length ? (
              <p className="mt-2 text-xs text-ink-muted">No classes awaiting a workload outcome are available.</p>
            ) : null}
          </div>
          <div>
            <label htmlFor="substitute-colleague" className={LABEL_CLASS}>Colleague</label>
            <select
              id="substitute-colleague"
              value={selectedColleague ? colleagueId : ""}
              onChange={(event) => { setColleagueId(event.target.value); selectionChanged(); }}
              disabled={issuing || !colleagues?.length}
              className={INPUT_CLASS}
            >
              <option value="">{colleagues === null && !loadError ? "Loading colleagues…" : "Choose a colleague"}</option>
              {(colleagues || []).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.full_name || professorLabel(entry.id)}{entry.employee_id ? ` · ${entry.employee_id}` : ""}
                </option>
              ))}
            </select>
            {colleagues?.length === 0 ? (
              <p className="mt-2 text-xs text-ink-muted">No colleagues found. Your account must have a department with other faculty members.</p>
            ) : null}
          </div>
        </div>
        {loadError ? (
          <StatusBanner tone="warning" title="Couldn't load colleagues">
            {describeWorkloadError(loadError)}
            <div className="mt-2">
              <Button variant="secondary" onClick={() => setRefreshVersion((value) => value + 1)}>Retry</Button>
            </div>
          </StatusBanner>
        ) : null}
        {error ? (
          <StatusBanner tone="error" title="Couldn't issue the key" detail={error.status ? error.message : undefined}>
            {describeWorkloadError(error)}
          </StatusBanner>
        ) : null}
        <div>
          <Button type="submit" loading={issuing} disabled={!selectedClass || !selectedColleague}>Issue Key</Button>
        </div>
      </form>
      {issued ? (
        <div className="mt-4 rounded-lg border border-success/30 bg-success-soft p-4" role="status">
          <p className="text-sm font-semibold text-success">Key issued for {issued.colleagueName}</p>
          <p className="mt-1 text-sm text-ink">{issued.classLabel}</p>
          <p className="mt-1 text-xs text-ink-muted">
            Expires {formatDateTime(issued.expires_at)} (Asia/Karachi; {issued.expires_in_minutes} minutes).
            Share this key only with that colleague; it can be redeemed once.
          </p>
          <label htmlFor="issued-substitute-key" className={`mt-3 block ${LABEL_CLASS}`}>Substitute key</label>
          <textarea
            id="issued-substitute-key"
            readOnly
            rows={3}
            value={issued.substitute_token}
            className={`${INPUT_CLASS} break-all font-mono text-xs`}
          />
          <div className="mt-2">
            <Button variant="secondary" onClick={copyKey}>{copied ? "Copied ✓" : "Copy"}</Button>
          </div>
          {copyError ? <p className="mt-2 text-xs text-warning">Clipboard unavailable. Select and copy the full key above.</p> : null}
        </div>
      ) : null}
    </section>
  );
}
