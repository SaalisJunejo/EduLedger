import { useEffect, useRef, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import Spinner from "../ui/Spinner.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import GradeCorrectionsCard from "../GradeCorrectionsCard.jsx";
import { api } from "../../lib/api.js";
import { describeGradesError } from "../../lib/gradesMessages.js";
import { ipfsGatewayUrl } from "../../lib/records.js";

const INPUT_CLASS = "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

export default function GradeCorrectionRequestCard() {
  const [studentId, setStudentId] = useState("");
  const [courseCode, setCourseCode] = useState("");
  const [newGrade, setNewGrade] = useState("");
  const [justification, setJustification] = useState("");
  const [version, setVersion] = useState(0);
  const [lookup, setLookup] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [result, setResult] = useState(null);
  const busy = useRef(false);
  const mounted = useRef(false);

  const normalizedStudentId = studentId.trim();
  const normalizedCourse = courseCode.trim();
  const validStudent = /^\d+$/.test(normalizedStudentId)
    && Number(normalizedStudentId) > 0 && Number(normalizedStudentId) <= 2147483647;
  const validCourse = normalizedCourse.length > 0 && Array.from(normalizedCourse).length <= 64;
  const selectionKey = validStudent && validCourse ? `${Number(normalizedStudentId)}:${normalizedCourse}` : null;
  const characterCount = Array.from(justification.trim()).length;
  // Key the snapshot to BOTH fields and the refresh version. Editing a field
  // immediately hides the old grade and disables submit, even before the effect.
  const currentLookup = selectionKey && lookup?.key === selectionKey && lookup.version === version ? lookup : null;
  const currentGrade = currentLookup?.grade;
  const loadingGrade = Boolean(selectionKey && (!currentLookup || currentLookup.loading));

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!selectionKey) {
      setLookup(null);
      return;
    }
    let active = true;
    setLookup({ key: selectionKey, version, loading: true });
    // Avoid issuing one request for every keystroke. Ignore late responses
    // when either field changes or this card unmounts.
    const timer = setTimeout(async () => {
      try {
        const data = await api(`/grades/${Number(normalizedStudentId)}`);
        const grade = data.grades.find((row) => row.courseCode === normalizedCourse);
        if (active) {
          setLookup({ key: selectionKey, version, loading: false, grade,
            error: grade ? null : { check: "grade", code: "grade_not_found" } });
        }
      } catch (error) {
        if (active) setLookup({ key: selectionKey, version, loading: false, error });
      }
    }, 300);
    return () => { active = false; clearTimeout(timer); };
  }, [selectionKey, normalizedStudentId, normalizedCourse, version]);

  function refresh() {
    setVersion((value) => value + 1);
  }

  function edit(setter, value) {
    setter(value);
    setSubmitError(null);
    setResult(null);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy.current) return;
    let message;
    if (!validStudent) message = "Enter a student ID between 1 and 2147483647.";
    else if (!validCourse) message = "Enter a course code of no more than 64 characters.";
    else if (!currentGrade || loadingGrade) message = "Load the current grade before requesting a correction.";
    else if (currentGrade.locked) message = "A correction is already pending for this grade";
    else if (!newGrade.trim() || Array.from(newGrade.trim()).length > 16) message = "Enter a new grade of 1 to 16 characters.";
    else if (newGrade.trim() === currentGrade.gradeValue) message = "The new grade must differ from the current grade.";
    else if (characterCount < 10) message = "Explain the correction in at least 10 characters, excluding leading and trailing spaces.";
    if (message) {
      setSubmitError({ message });
      return;
    }

    busy.current = true;
    setSubmitting(true);
    setSubmitError(null);
    setResult(null);
    try {
      // The backend reads the current grade, pins the justification JSON,
      // and locks the row. No client-supplied old grade or evidence upload.
      const data = await api("/grades/corrections", {
        method: "POST",
        body: {
          student_id: Number(normalizedStudentId),
          course_code: normalizedCourse,
          new_grade: newGrade.trim(),
          justification: justification.trim(),
        },
      });
      if (!mounted.current) return;
      setResult(data);
      setNewGrade("");
      setJustification("");
    } catch (error) {
      if (mounted.current) setSubmitError(error);
    } finally {
      busy.current = false;
      if (mounted.current) {
        setSubmitting(false);
        // Re-read the grade and My requests on success or a conflict such as
        // grade_locked/stale_value, retaining the user's draft on failure.
        refresh();
      }
    }
  }

  const canSubmit = currentGrade && !currentGrade.locked && !loadingGrade
    && newGrade.trim() && Array.from(newGrade.trim()).length <= 16
    && newGrade.trim() !== currentGrade.gradeValue && characterCount >= 10;

  return (
    <div className="flex flex-col gap-5">
      <Card title="Grade correction request"
        description="Request a change to an existing grade. Your justification is pinned as IPFS evidence; the grade stays locked until the HOD approves or rejects the correction.">
        <form onSubmit={submit} noValidate className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="grade-student" className={LABEL_CLASS}>Student ID</label>
              <input id="grade-student" type="text" inputMode="numeric" value={studentId}
                onChange={(event) => edit(setStudentId, event.target.value)} disabled={submitting}
                placeholder="Student user ID" required className={INPUT_CLASS} />
            </div>
            <div>
              <label htmlFor="grade-course" className={LABEL_CLASS}>Course code</label>
              <input id="grade-course" value={courseCode} onChange={(event) => edit(setCourseCode, event.target.value)}
                disabled={submitting} placeholder="e.g. BSCS-401" required className={INPUT_CLASS} />
            </div>
            <div>
              <label htmlFor="grade-current" className={LABEL_CLASS}>Current grade (read-only)</label>
              <input id="grade-current" value={currentGrade?.gradeValue ?? ""} readOnly aria-busy={loadingGrade}
                placeholder={loadingGrade ? "Loading…" : "Enter student ID and course code"}
                className={`${INPUT_CLASS} bg-surface-muted`} />
            </div>
            <div>
              <label htmlFor="grade-new" className={LABEL_CLASS}>New grade</label>
              <input id="grade-new" value={newGrade} onChange={(event) => edit(setNewGrade, event.target.value)}
                disabled={submitting} placeholder="e.g. A-" required className={INPUT_CLASS} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="secondary" onClick={refresh} disabled={submitting || loadingGrade || !selectionKey}>
              Refresh current grade
            </Button>
            {loadingGrade ? <span className="flex items-center gap-2 text-sm text-ink-muted" role="status"><Spinner /> Loading current grade…</span> : null}
          </div>
          {(studentId.trim() && !validStudent) || (normalizedCourse && !validCourse) ? (
            <p className="text-sm text-danger-strong" role="alert">Use a positive student ID up to 2147483647 and a course code of at most 64 characters.</p>
          ) : null}
          {currentLookup?.error ? (
            <StatusBanner tone="error" title="Couldn't load the current grade">{describeGradesError(currentLookup.error)}</StatusBanner>
          ) : null}
          {currentGrade?.locked ? (
            <StatusBanner tone="warning" title="Locked">A correction is already pending for this grade</StatusBanner>
          ) : null}
          <div>
            <label htmlFor="grade-justification" className={LABEL_CLASS}>Justification</label>
            <textarea id="grade-justification" rows={4} value={justification} required
              onChange={(event) => edit(setJustification, event.target.value)} disabled={submitting}
              aria-describedby="grade-justification-count" className={INPUT_CLASS}
              placeholder="Explain why this grade should be corrected." />
            <p id="grade-justification-count" className={`mt-1 text-xs ${characterCount >= 10 ? "text-success" : "text-ink-muted"}`}>
              {characterCount} characters · minimum 10 (excluding leading and trailing spaces)
            </p>
          </div>
          {submitError ? (
            <StatusBanner tone="error" title="Couldn't submit the grade correction">{describeGradesError(submitError)}</StatusBanner>
          ) : null}
          {result ? (
            <StatusBanner tone="success" title={`Grade correction #${result.correction.onchainProposalId} submitted`}>
              Pending HOD review. The current grade is unchanged and locked.
              <p className="mt-2 break-all font-mono text-xs">Transaction: {result.transaction_hash}</p>
              <a href={ipfsGatewayUrl(result.correction.ipfsCid)} target="_blank" rel="noreferrer"
                className="mt-2 inline-block font-semibold underline">View IPFS evidence ↗</a>
            </StatusBanner>
          ) : null}
          <div>
            <Button type="submit" loading={submitting} disabled={!canSubmit}>
              {submitting ? "Submitting grade correction…" : "Submit grade correction"}
            </Button>
            {submitting ? <p className="mt-2 text-xs text-ink-muted" role="status">Pinning justification evidence and recording the grade correction on-chain…</p> : null}
          </div>
        </form>
      </Card>
      <GradeCorrectionsCard title="My requests" refreshKey={version} onRefresh={refresh} />
    </div>
  );
}
