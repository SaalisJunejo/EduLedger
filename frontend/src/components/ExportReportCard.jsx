import { useEffect, useMemo, useState } from "react";
import Button from "./ui/Button.jsx";
import Card from "./ui/Card.jsx";
import Spinner from "./ui/Spinner.jsx";
import StatusBanner from "./ui/StatusBanner.jsx";
import { apiDownload } from "../lib/api.js";
import { describeApiError } from "../lib/attendanceMessages.js";
import { formatDateTime, loadTimetableClasses, professorLabel } from "../lib/workload.js";

const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

const FORMATS = [
  { id: "xlsx", label: "Excel (.xlsx)" },
  { id: "csv", label: "CSV (.csv)" },
];

// The backend reads `month` as a calendar month in TIMETABLE_TIMEZONE, so
// "this month" is computed in the same zone the rest of the page uses.
function currentMonth() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Karachi", year: "numeric", month: "2-digit" })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}`;
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function classLabel(entry) {
  return `${entry.courseCode} · Room ${entry.room} · ${formatDateTime(entry.startTime)}`;
}

/**
 * "Export monthly report" (requirements doc section 3.5): the month's
 * on-chain-locked check-ins of one or all accessible classes, downloaded
 * through an authenticated fetch so the bearer token is sent.
 *
 * `classes` is the list the host page already loaded — GET /faculty/classes
 * on the instructor dashboard, GET /timetable/classes on the HOD/Admin ones.
 * With `showFacultyFilter` the card adds a faculty dropdown (built from the
 * professors in `classes`) that narrows the class picker; picking a faculty
 * member and leaving the class on "All classes" exports that faculty
 * member's classes only (professor_id).
 */
export default function ExportReportCard({ classes, showFacultyFilter = false }) {
  const [month, setMonth] = useState(currentMonth);
  const [format, setFormat] = useState("xlsx");
  const [facultyId, setFacultyId] = useState("");
  const [classId, setClassId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(null);

  const faculty = useMemo(() => {
    if (!showFacultyFilter) return [];
    const names = new Map();
    for (const entry of classes || []) {
      if (entry.professorId == null || names.has(entry.professorId)) continue;
      names.set(entry.professorId, entry.professorName || professorLabel(entry.professorId));
    }
    return [...names]
      .map(([id, name]) => ({ id, name }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [classes, showFacultyFilter]);

  const visibleClasses = useMemo(() => {
    const entries = classes || [];
    if (!showFacultyFilter || !facultyId) return entries;
    return entries.filter((entry) => String(entry.professorId) === facultyId);
  }, [classes, facultyId, showFacultyFilter]);

  async function exportReport(event) {
    event.preventDefault();
    if (busy || !month) return;
    setBusy(true);
    setError(null);
    setSaved(null);
    const params = new URLSearchParams({ month, format });
    if (classId) params.set("class_id", classId);
    // Without a class the faculty pick is the scope; with one it is implied
    // by the class itself, so sending both only risks a mismatch error.
    else if (showFacultyFilter && facultyId) params.set("professor_id", facultyId);
    try {
      const { blob, filename } = await apiDownload(
        `/attendance/export?${params}`,
        `eduledger-attendance-${month}.${format}`,
      );
      saveBlob(blob, filename);
      setSaved(filename);
    } catch (exportError) {
      setError(exportError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Export monthly report"
      description="Every check-in locked on-chain during the month (Asia/Karachi), from both attendance and workload sessions, with each row's transaction hash. The Excel file adds a Summary sheet with the lectures attended and courses covered per student."
    >
      <form onSubmit={exportReport} className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className={LABEL_CLASS}>Month</span>
          <input
            type="month"
            required
            value={month}
            onChange={(event) => setMonth(event.target.value)}
            disabled={busy}
            className={INPUT_CLASS}
          />
        </label>

        {showFacultyFilter ? (
          <label className="flex flex-col gap-1.5">
            <span className={LABEL_CLASS}>Faculty</span>
            <select
              value={facultyId}
              onChange={(event) => {
                setFacultyId(event.target.value);
                // The previous pick may belong to another professor.
                setClassId("");
              }}
              disabled={busy}
              className={INPUT_CLASS}
            >
              <option value="">All faculty</option>
              {faculty.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name} · {professorLabel(entry.id)}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <label className="flex flex-col gap-1.5">
          <span className={LABEL_CLASS}>Class</span>
          <select
            value={classId}
            onChange={(event) => setClassId(event.target.value)}
            disabled={busy}
            className={INPUT_CLASS}
          >
            <option value="">{showFacultyFilter ? "All classes" : "All my classes"}</option>
            {visibleClasses.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {classLabel(entry)}
              </option>
            ))}
          </select>
        </label>

        <fieldset className="flex flex-col gap-1.5 sm:col-span-2">
          <legend className={`${LABEL_CLASS} mb-1.5`}>Format</legend>
          <div className="flex flex-wrap gap-2">
            {FORMATS.map((entry) => (
              <label
                key={entry.id}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${
                  format === entry.id ? "border-primary text-primary" : "border-line text-ink"
                }`}
              >
                <input
                  type="radio"
                  name="export-format"
                  value={entry.id}
                  checked={format === entry.id}
                  onChange={() => setFormat(entry.id)}
                  disabled={busy}
                  className="accent-primary"
                />
                {entry.label}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="sm:col-span-2">
          <Button type="submit" loading={busy} disabled={!month} className="sm:w-44">
            Export
          </Button>
        </div>
      </form>

      {error ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Couldn't export the report" detail={error.message}>
            {describeApiError(error)}
          </StatusBanner>
        </div>
      ) : null}
      {saved ? (
        <div className="mt-4">
          <StatusBanner tone="success" title="Report downloaded">
            Saved as {saved}.
          </StatusBanner>
        </div>
      ) : null}
    </Card>
  );
}

/**
 * The same card for the HOD and Admin dashboards: it loads the
 * institution-wide class list (GET /timetable/classes, HOD/ADMIN only) once
 * and renders the export card with the faculty filter enabled.
 */
export function InstitutionExportCard() {
  const [classes, setClasses] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    loadTimetableClasses()
      .then((rows) => { if (active) setClasses(rows); })
      .catch((loadError) => { if (active) setError(loadError); });
    return () => { active = false; };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {error ? (
        <StatusBanner tone="warning" title="Couldn't load the class list" detail={error.message}>
          {describeApiError(error)} You can still export a whole month with “All classes”.
        </StatusBanner>
      ) : null}
      {classes === null && !error ? (
        <div className="flex items-center gap-2 text-sm text-ink-muted" role="status">
          <Spinner /> Loading the class list…
        </div>
      ) : null}
      <ExportReportCard classes={classes || []} showFacultyFilter />
    </div>
  );
}
