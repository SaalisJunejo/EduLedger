import { useEffect, useMemo, useRef, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import Spinner from "../components/ui/Spinner.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api, apiDownload } from "../lib/api.js";
import { describeApiError } from "../lib/attendanceMessages.js";

/**
 * Phase 2.10 attendance portal (PRD v2.2 sections 4.9.7-4.9.12).
 *
 * Read-only. Every status, count and percentage on this page is rendered from
 * the Phase 2.9 report payload; nothing here writes attendance or recomputes a
 * value the backend already decided. Filters and the tab live in the URL, so a
 * reload or browser navigation restores the same report, and the same filter
 * values are sent to the sessions, matrix, roster, download and verify calls.
 */

const TABS = [
  { id: "sessions", label: "Sessions" },
  { id: "matrix", label: "Matrix" },
];

const RANGES = [
  { id: "week", label: "This week" },
  { id: "month", label: "This month" },
  { id: "semester", label: "This semester" },
  { id: "custom", label: "Custom" },
];

// The values are the backend's occurrence statuses (see services/attendance_portal).
// CONDUCTED is the outcome family and also matches LATE/ZERO on the backend.
const STATUS_FILTERS = [
  { value: "ALL", label: "All statuses" },
  { value: "SCHEDULED", label: "Scheduled" },
  { value: "CONDUCTED", label: "Conducted (incl. late and 0 present)" },
  { value: "CONDUCTED_LATE", label: "Conducted late" },
  { value: "CONDUCTED_ZERO", label: "Conducted - 0 present" },
  { value: "UNCONDUCTED", label: "Unconducted" },
  { value: "HOLIDAY", label: "Holiday" },
  { value: "CANCELLED", label: "Cancelled" },
];

// Design-foundation chip recipe (same as the workload audit log).
const CHIP = "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold";
const STATUS_CHIP = {
  CONDUCTED: "bg-surface-muted text-ink",
  CONDUCTED_LATE: "bg-warning-soft text-warning",
  CONDUCTED_ZERO: "bg-warning-soft text-warning",
  UNCONDUCTED: "bg-danger-soft text-danger-strong",
  HOLIDAY: "bg-surface-muted text-ink-muted",
  CANCELLED: "bg-surface-muted text-ink-muted",
  SCHEDULED: "border border-line bg-surface text-ink",
};
const SELECT_CLASS = "mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const TH_BASE = "border-b border-line bg-surface-muted px-3 py-2 text-xs font-bold uppercase tracking-wide text-ink-muted";
const TH_CLASS = `${TH_BASE} text-left`;
const TH_CENTER = `${TH_BASE} text-center`;
const TD_CLASS = "border-b border-line px-3 py-2 align-middle text-sm text-ink";
const DEFAULT_ZONE = "Asia/Karachi";

function utcDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

function shiftDay(iso, days) {
  const date = utcDate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDay(date);
}

// "Today" must be resolved in the report's timezone: the backend reads every
// date bound and every occurrence in it (TIMETABLE_TIMEZONE).
function zoneToday(zone) {
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" })
        .formatToParts(new Date())
        .map((part) => [part.type, part.value]),
    );
    return `${parts.year}-${parts.month}-${parts.day}`;
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

// "This semester" sends no bounds so the server uses the term's own dates; it
// is never approximated with a client-side month count.
function presetDates(preset, zone) {
  if (preset === "week") {
    const today = zoneToday(zone);
    const monday = shiftDay(today, -((utcDate(today).getUTCDay() + 6) % 7));
    return { from: monday, to: shiftDay(monday, 6) };
  }
  if (preset === "month") {
    const today = zoneToday(zone);
    const first = `${today.slice(0, 8)}01`;
    const next = utcDate(first);
    next.setUTCMonth(next.getUTCMonth() + 1);
    return { from: first, to: shiftDay(isoDay(next), -1) };
  }
  return { from: "", to: "" };
}

function reportQuery(filters) {
  const params = new URLSearchParams();
  if (filters.term) params.set("term", filters.term);
  if (filters.section) params.set("section", filters.section);
  params.set("course", filters.course || "all");
  params.set("status", filters.status || "ALL");
  if (filters.from) params.set("date_from", filters.from);
  if (filters.to) params.set("date_to", filters.to);
  if (filters.q) params.set("q", filters.q);
  return params;
}

function formatDate(iso) {
  if (!iso) return "-";
  const date = utcDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric",
  });
}

function shortDate(iso) {
  if (!iso) return "-";
  const date = utcDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", { timeZone: "UTC", day: "numeric", month: "short" });
}

function formatStamp(value, zone) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try {
    return date.toLocaleString("en-US", {
      timeZone: zone, year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function statusLabel(session) {
  switch (session.status) {
    case "CONDUCTED":
      return "Conducted";
    case "CONDUCTED_LATE":
      return session.lateMinutes == null ? "Late" : `Late ${session.lateMinutes}m`;
    case "CONDUCTED_ZERO":
      return "0 present";
    case "UNCONDUCTED":
      return "Unconducted";
    case "HOLIDAY":
      return "Holiday";
    case "CANCELLED":
      return "Cancelled";
    case "SCHEDULED":
      return "Scheduled";
    default:
      return session.status || "Unknown";
  }
}

// Phase 2.9's session payload does not attach the closure reason yet, so this
// only renders a reason when a payload actually supplies one. It never invents
// a reason for a holiday.
function closureReason(session) {
  return session.closureReason || session.holidayReason || session.closure?.reason || "";
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

function StatusChip({ session }) {
  return (
    <span className={`${CHIP} ${STATUS_CHIP[session.status] || "bg-surface-muted text-ink-muted"}`}>
      {statusLabel(session)}
    </span>
  );
}

function MatrixCell({ cell, session }) {
  if (!cell || cell.attendance == null) {
    return (
      <td
        className={`${TD_CLASS} text-center text-ink-muted`}
        title={cell ? `${session ? `${formatDate(session.date)} - ` : ""}not conducted` : "Not this student's section"}
      >
        <span aria-hidden="true">{"\u2014"}</span>
        <span className="sr-only">{cell ? "Not conducted" : "No session for this student's section"}</span>
      </td>
    );
  }
  const present = cell.attendance === "PRESENT";
  return (
    <td className={`${TD_CLASS} text-center`}>
      <span className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold ${
        present ? "bg-success-soft text-success" : "bg-danger-soft text-danger-strong"
      }`}>
        <span aria-hidden="true">{present ? "\u2713" : "\u2717"}</span>
        <span className="sr-only">{present ? "Present" : "Absent"}</span>
      </span>
    </td>
  );
}

export default function Attendance() {
  const user = useOutletContext();
  const [params, setParams] = useSearchParams();

  const tab = TABS.some((entry) => entry.id === params.get("tab")) ? params.get("tab") : "sessions";
  const termParam = params.get("term") || "";
  const sectionParam = params.get("section") || "";
  const courseParam = params.get("course") || "all";
  const rawStatus = (params.get("status") || "ALL").toUpperCase();
  const status = STATUS_FILTERS.some((entry) => entry.value === rawStatus) ? rawStatus : "ALL";
  const rangeParam = RANGES.some((entry) => entry.id === params.get("range")) ? params.get("range") : "semester";
  const fromParam = params.get("from") || "";
  const toParam = params.get("to") || "";
  const qParam = params.get("q") || "";

  const [searchText, setSearchText] = useState(qParam);
  const searchRef = useRef(null);

  const [catalogState, setCatalogState] = useState({ data: null, error: null });
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const settledCatalog = useRef(null);

  const [attempt, setAttempt] = useState(0);
  const [sessionsState, setSessionsState] = useState({ key: null, data: null, error: null });
  const [matrixState, setMatrixState] = useState({ key: null, data: null, error: null });

  const [selected, setSelected] = useState(null);
  const [rosterState, setRosterState] = useState({ key: null, data: null, error: null });
  const [rosterAttempt, setRosterAttempt] = useState(0);

  const [downloadState, setDownloadState] = useState({ busy: null, error: null, saved: null });
  const [verifyState, setVerifyState] = useState({ busy: false, result: null, sessions: [], error: null });

  const fetched = useRef({ sessions: null, matrix: null, roster: null });
  const filterKeyRef = useRef("");
  const timezone = catalogState.data?.timezone || DEFAULT_ZONE;

  // Filter choices come from one least-restrictive report call (term only):
  // no staff-visible course/section directory exists for FACULTY, and this
  // call is scoped by the backend exactly like the report itself.
  useEffect(() => {
    // Writing the resolved term into the URL must not refetch a catalogue
    // that already describes it.
    const settled = settledCatalog.current;
    if (termParam && settled?.term && String(settled.term.id) === termParam) {
      // Reset filters drops the term, and the effect below writes the
      // resolved term straight back in the same commit. The term-less request
      // that reset started is cancelled by this re-run but has already
      // cleared the state, so the choices would load forever. Restoring only
      // fills an empty state: a loaded or failed result is never replaced by
      // an older response.
      setCatalogState((prev) => (prev.data || prev.error ? prev : { data: settled, error: null }));
      return undefined;
    }
    let active = true;
    const suffix = termParam ? `?${new URLSearchParams({ term: termParam })}` : "";
    setCatalogState({ data: null, error: null });
    api(`/faculty/sessions${suffix}`).then((data) => {
      if (!active) return;
      settledCatalog.current = data;
      setCatalogState({ data, error: null });
    }).catch((error) => {
      if (!active) return;
      settledCatalog.current = null;
      setCatalogState({ data: null, error });
    });
    return () => { active = false; };
  }, [termParam, catalogAttempt]);

  const catalog = catalogState.data;
  const catalogError = catalogState.error;
  const term = catalog?.term || null;

  // Make the resolved term shareable, like the timetable page does.
  useEffect(() => {
    if (!term || termParam) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (!next.get("term")) next.set("term", String(term.id));
      return next;
    }, { replace: true });
  }, [term, termParam, setParams]);

  const sectionChoices = useMemo(() => {
    const choices = new Map();
    for (const row of catalog?.rows || []) {
      if (row.sectionId == null) continue;
      choices.set(String(row.sectionId), row.section || `Section #${row.sectionId}`);
    }
    if (sectionParam && !choices.has(sectionParam)) choices.set(sectionParam, `Section #${sectionParam}`);
    return [...choices.entries()]
      .sort((left, right) => left[1].localeCompare(right[1]))
      .map(([id, label]) => ({ id, label }));
  }, [catalog, sectionParam]);

  const courseChoices = useMemo(() => {
    const codes = new Set((catalog?.rows || []).map((row) => row.courseCode).filter(Boolean));
    if (courseParam !== "all") codes.add(courseParam);
    return [...codes].sort((left, right) => left.localeCompare(right));
  }, [catalog, courseParam]);

  const termChoices = useMemo(() => {
    const choices = new Map();
    if (term) choices.set(String(term.id), term);
    if (termParam && !choices.has(termParam)) choices.set(termParam, { id: termParam, name: `Term #${termParam}` });
    return [...choices.values()].sort((left, right) => String(left.id).localeCompare(String(right.id)));
  }, [term, termParam]);

  const resolvedRange = useMemo(() => (
    rangeParam === "custom"
      ? { from: fromParam, to: toParam }
      : presetDates(rangeParam, timezone)
  ), [rangeParam, fromParam, toParam, timezone]);

  const customIncomplete = rangeParam === "custom" && (!fromParam || !toParam);
  // Wait for the catalog so the term is known (and the timezone resolved)
  // before the report is requested.
  const termReady = Boolean(catalogError) || Boolean(termParam) || Boolean(catalog && !term);
  const canQuery = !customIncomplete && termReady;

  const query = useMemo(() => reportQuery({
    term: termParam,
    section: sectionParam,
    course: courseParam,
    status,
    from: resolvedRange.from,
    to: resolvedRange.to,
    q: qParam,
  }), [termParam, sectionParam, courseParam, status, resolvedRange.from, resolvedRange.to, qParam]);
  const filterKey = query.toString();
  filterKeyRef.current = filterKey;

  // The report is read-only; both tabs share these filters exactly.
  useEffect(() => {
    if (tab !== "sessions" || !canQuery) return undefined;
    const requestKey = `${filterKey}|${attempt}`;
    if (fetched.current.sessions === requestKey) return undefined;
    let active = true;
    setSessionsState({ key: filterKey, data: null, error: null });
    api(`/faculty/sessions?${filterKey}`).then((data) => {
      // Only a settled request is cached: an abandoned one (tab switch or
      // filter change) must be retried, never left as a permanent spinner.
      if (!active) return;
      fetched.current.sessions = requestKey;
      setSessionsState({ key: filterKey, data, error: null });
    }).catch((error) => {
      if (!active) return;
      fetched.current.sessions = requestKey;
      setSessionsState({ key: filterKey, data: null, error });
    });
    return () => { active = false; };
  }, [tab, canQuery, filterKey, attempt]);

  useEffect(() => {
    if (tab !== "matrix" || !canQuery) return undefined;
    const requestKey = `${filterKey}|${attempt}`;
    if (fetched.current.matrix === requestKey) return undefined;
    let active = true;
    setMatrixState({ key: filterKey, data: null, error: null });
    api(`/faculty/attendance/matrix?${filterKey}&limit=500&offset=0`).then((data) => {
      if (!active) return;
      fetched.current.matrix = requestKey;
      setMatrixState({ key: filterKey, data, error: null });
    }).catch((error) => {
      if (!active) return;
      fetched.current.matrix = requestKey;
      setMatrixState({ key: filterKey, data: null, error });
    });
    return () => { active = false; };
  }, [tab, canQuery, filterKey, attempt]);

  const sessionsData = sessionsState.key === filterKey ? sessionsState.data : null;
  const sessionsError = sessionsState.key === filterKey ? sessionsState.error : null;
  const matrixData = matrixState.key === filterKey ? matrixState.data : null;
  const matrixError = matrixState.key === filterKey ? matrixState.error : null;

  const selectedRow = selected && sessionsData
    ? sessionsData.rows.find((row) => row.key === selected.key && row.occurrenceId != null) || null
    : null;
  const rosterOccurrenceId = selectedRow?.occurrenceId ?? null;
  const rosterKey = rosterOccurrenceId == null ? null : `${rosterOccurrenceId}:${filterKey}`;

  useEffect(() => {
    if (rosterOccurrenceId == null) return undefined;
    const requestKey = `${rosterOccurrenceId}|${filterKey}|${rosterAttempt}`;
    if (fetched.current.roster === requestKey) return undefined;
    let active = true;
    setRosterState({ key: rosterKey, data: null, error: null });
    api(`/faculty/sessions/${rosterOccurrenceId}/roster?${filterKey}`).then((data) => {
      if (!active) return;
      fetched.current.roster = requestKey;
      setRosterState({ key: rosterKey, data, error: null });
    }).catch((error) => {
      if (!active) return;
      fetched.current.roster = requestKey;
      setRosterState({ key: rosterKey, data: null, error });
    });
    return () => { active = false; };
  }, [rosterOccurrenceId, rosterKey, filterKey, rosterAttempt]);

  const rosterData = rosterState.key === rosterKey ? rosterState.data : null;
  const rosterError = rosterState.key === rosterKey ? rosterState.error : null;
  const rosterLoading = Boolean(rosterKey) && !rosterData && !rosterError;

  // Downloads and verification describe the filters that were on screen when
  // they ran; a filter change retires their message instead of relabelling it.
  useEffect(() => {
    setDownloadState({ busy: null, error: null, saved: null });
    setVerifyState({ busy: false, result: null, sessions: [], error: null });
  }, [filterKey]);

  // Search stays responsive, then lands in the URL like every other filter.
  useEffect(() => {
    if (searchRef.current !== document.activeElement) setSearchText(qParam);
  }, [qParam]);

  useEffect(() => {
    const trimmed = searchText.trim();
    if (trimmed === qParam) return undefined;
    const timer = setTimeout(() => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
        return next;
      });
    }, 350);
    return () => clearTimeout(timer);
  }, [searchText, qParam, setParams]);

  function changeFilter(key, value) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      const clears = value === "" || value == null
        || (key === "status" && value === "ALL")
        || (key === "course" && value === "all");
      if (clears) {
        next.delete(key);
      } else {
        next.set(key, String(value));
      }
      return next;
    });
  }

  function chooseRange(nextRange) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("range", nextRange);
      if (nextRange === "custom") {
        if (!next.get("from")) next.set("from", resolvedRange.from || term?.startDate || "");
        if (!next.get("to")) next.set("to", resolvedRange.to || term?.endDate || "");
      } else {
        next.delete("from");
        next.delete("to");
      }
      return next;
    });
  }

  function resetFilters() {
    setSearchText("");
    setSelected(null);
    setParams((prev) => {
      const next = new URLSearchParams();
      const currentTab = prev.get("tab");
      if (currentTab) next.set("tab", currentTab);
      return next;
    });
  }

  function changeTab(nextTab) {
    if (nextTab === tab) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", nextTab);
      return next;
    });
  }

  function tabKeyDown(event, index) {
    const moves = {
      ArrowRight: (index + 1) % TABS.length,
      ArrowLeft: (index + TABS.length - 1) % TABS.length,
      Home: 0,
      End: TABS.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = TABS[moves[event.key]];
    changeTab(next.id);
    document.getElementById(`attendance-tab-${next.id}`)?.focus();
  }

  async function download(format) {
    const requestKey = filterKeyRef.current;
    setDownloadState({ busy: format, error: null, saved: null });
    try {
      const downloadParams = new URLSearchParams(query);
      downloadParams.set("format", format);
      const { blob, filename } = await apiDownload(
        `/faculty/attendance?${downloadParams.toString()}`,
        `eduledger-attendance.${format}`,
      );
      saveBlob(blob, filename);
      if (filterKeyRef.current === requestKey) {
        setDownloadState({ busy: null, error: null, saved: filename });
      }
    } catch (error) {
      if (filterKeyRef.current === requestKey) {
        setDownloadState({ busy: null, error, saved: null });
      }
    }
  }

  async function verify() {
    const requestKey = filterKeyRef.current;
    // The sessions the verification can name, captured for the same filters.
    const known = tab === "sessions" ? (sessionsData?.rows || []) : (matrixData?.sessions || []);
    setVerifyState({ busy: true, result: null, sessions: known, error: null });
    try {
      const verifyParams = new URLSearchParams(query);
      verifyParams.set("limit", "500");
      verifyParams.set("offset", "0");
      const result = await api(`/timetable/verify?${verifyParams.toString()}`);
      if (filterKeyRef.current === requestKey) {
        setVerifyState({ busy: false, result, sessions: known, error: null });
      }
    } catch (error) {
      if (filterKeyRef.current === requestKey) {
        setVerifyState({ busy: false, result: null, sessions: known, error });
      }
    }
  }

  const panelData = tab === "sessions" ? sessionsData : matrixData;
  const panelError = tab === "sessions" ? sessionsError : matrixError;
  const panelLoading = canQuery && !panelData && !panelError;
  const rows = panelData?.rows || [];

  const mismatchGroups = useMemo(() => {
    const result = verifyState.result;
    if (!result || !result.mismatches.length) return [];
    const byOccurrence = new Map(
      verifyState.sessions
        .filter((session) => session.occurrenceId != null)
        .map((session) => [session.occurrenceId, session]),
    );
    const groups = new Map();
    for (const mismatch of result.mismatches) {
      const key = mismatch.occurrenceId == null ? "unknown" : String(mismatch.occurrenceId);
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          session: byOccurrence.get(mismatch.occurrenceId) || null,
          entries: [],
        });
      }
      groups.get(key).entries.push(mismatch);
    }
    return [...groups.values()];
  }, [verifyState.result, verifyState.sessions]);

  const rangeSummary = rangeParam === "semester"
    ? (term ? `Semester dates: ${term.startDate} to ${term.endDate} (resolved by the server).` : "Semester dates are resolved by the server.")
    : (resolvedRange.from && resolvedRange.to
      ? `Dates: ${resolvedRange.from} to ${resolvedRange.to} (${timezone}).`
      : `Dates: choose both custom bounds (${timezone}).`);

  return (
    <PageLayout
      role={user.role}
      title="Attendance"
      wide
      description="Read-only attendance for your classes and department. Statuses, counts and percentages come from the backend report; this page never changes attendance. Times are shown in the report timezone."
    >
      <Card title="Filters" description="Every control below is part of the report request. The same filters drive both tabs, the roster, the downloads and verification.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block" htmlFor="attendance-term">
            <span className={LABEL_CLASS}>Term</span>
            <select
              id="attendance-term"
              className={SELECT_CLASS}
              value={termParam || (term ? String(term.id) : "")}
              disabled={!termChoices.length}
              onChange={(event) => changeFilter("term", event.target.value)}
            >
              {!termChoices.length ? (
                <option value="">
                  {catalogError ? "Term unavailable" : catalog ? "No term available" : "Loading terms..."}
                </option>
              ) : null}
              {termChoices.map((entry) => (
                <option key={entry.id} value={String(entry.id)}>
                  {entry.name}{entry.startDate ? ` (${entry.startDate} to ${entry.endDate})` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="block" htmlFor="attendance-section">
            <span className={LABEL_CLASS}>Section</span>
            <select
              id="attendance-section"
              className={SELECT_CLASS}
              value={sectionParam}
              onChange={(event) => changeFilter("section", event.target.value)}
            >
              <option value="">All sections</option>
              {sectionChoices.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.label}</option>
              ))}
            </select>
          </label>

          <label className="block" htmlFor="attendance-course">
            <span className={LABEL_CLASS}>Course</span>
            <select
              id="attendance-course"
              className={SELECT_CLASS}
              value={courseParam}
              onChange={(event) => changeFilter("course", event.target.value)}
            >
              <option value="all">All courses</option>
              {courseChoices.map((code) => (
                <option key={code} value={code}>{code}</option>
              ))}
            </select>
          </label>

          <label className="block" htmlFor="attendance-status">
            <span className={LABEL_CLASS}>Status</span>
            <select
              id="attendance-status"
              className={SELECT_CLASS}
              value={status}
              onChange={(event) => changeFilter("status", event.target.value)}
            >
              {STATUS_FILTERS.map((entry) => (
                <option key={entry.value} value={entry.value}>{entry.label}</option>
              ))}
            </select>
          </label>

          <label className="block" htmlFor="attendance-range">
            <span className={LABEL_CLASS}>Date range</span>
            <select
              id="attendance-range"
              className={SELECT_CLASS}
              value={rangeParam}
              onChange={(event) => chooseRange(event.target.value)}
            >
              {RANGES.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.label}</option>
              ))}
            </select>
          </label>

          {rangeParam === "custom" ? (
            <>
              <label className="block" htmlFor="attendance-from">
                <span className={LABEL_CLASS}>From</span>
                <input
                  id="attendance-from"
                  type="date"
                  className={SELECT_CLASS}
                  value={fromParam}
                  onChange={(event) => changeFilter("from", event.target.value)}
                />
              </label>
              <label className="block" htmlFor="attendance-to">
                <span className={LABEL_CLASS}>To</span>
                <input
                  id="attendance-to"
                  type="date"
                  className={SELECT_CLASS}
                  value={toParam}
                  onChange={(event) => changeFilter("to", event.target.value)}
                />
              </label>
            </>
          ) : null}

          <label className="block" htmlFor="attendance-search">
            <span className={LABEL_CLASS}>Student search</span>
            <input
              id="attendance-search"
              ref={searchRef}
              type="search"
              placeholder="Name or roll number"
              className={SELECT_CLASS}
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
            />
          </label>

          <div className="flex items-end">
            <Button variant="secondary" className="w-full" onClick={resetFilters}>Reset filters</Button>
          </div>
        </div>

        <p className="mt-3 text-xs text-ink-muted">{rangeSummary}</p>
        {termChoices.length <= 1 ? (
          <p className="mt-1 text-xs text-ink-muted">
            The attendance API exposes only the active term to this role, so the term filter shows the term this report is scoped to.
          </p>
        ) : null}
        {!catalog && !catalogError ? (
          <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-muted">
            <Spinner /> Loading filter choices...
          </p>
        ) : null}
        {catalogError && !panelError ? (
          <StatusBanner tone="error" title="Couldn't load filter choices" className="mt-3">
            {describeApiError(catalogError)}
            <Button variant="secondary" className="mt-2" onClick={() => setCatalogAttempt((value) => value + 1)}>
              Retry
            </Button>
          </StatusBanner>
        ) : null}
      </Card>

      <div role="tablist" aria-label="Attendance views" className="flex flex-wrap gap-2">
        {TABS.map((entry, index) => (
          <Button
            key={entry.id}
            id={`attendance-tab-${entry.id}`}
            role="tab"
            aria-selected={tab === entry.id}
            aria-controls="attendance-panel"
            tabIndex={tab === entry.id ? 0 : -1}
            variant={tab === entry.id ? "primary" : "secondary"}
            onClick={() => changeTab(entry.id)}
            onKeyDown={(event) => tabKeyDown(event, index)}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      <div
        id="attendance-panel"
        role="tabpanel"
        aria-labelledby={`attendance-tab-${tab}`}
        className="flex flex-col gap-5"
      >
        {customIncomplete ? (
          <StatusBanner tone="info" title="Choose both custom dates">
            A custom range needs a start and an end date. Nothing was requested yet, so no filter has been silently replaced.
          </StatusBanner>
        ) : null}

        <Card
          title={tab === "sessions" ? "Sessions" : "Attendance matrix"}
          description={tab === "sessions"
            ? "Select a session to open its roster. Status and counts are the values the backend returned."
            : "Students are rows, sessions are columns. The percentage is the backend's figure; a dash means no classes were conducted."}
        >
          {!canQuery ? (
            customIncomplete ? (
              <p className="text-sm text-ink-muted">Choose both custom dates above; nothing is requested until the range is complete.</p>
            ) : (
              <p className="text-sm text-ink-muted">Waiting for the filter choices above...</p>
            )
          ) : panelError ? (
            <StatusBanner
              tone="error"
              title={panelError.code === "out_of_scope" ? "These filters are outside your attendance scope" : "Couldn't load this attendance report"}
            >
              {describeApiError(panelError)}
              <Button variant="secondary" className="mt-2" onClick={() => setAttempt((value) => value + 1)}>
                Retry
              </Button>
            </StatusBanner>
          ) : panelLoading ? (
            <p role="status" className="flex items-center gap-2 text-sm text-ink-muted">
              <Spinner /> Loading {tab === "sessions" ? "sessions" : "the matrix"}...
            </p>
          ) : !panelData || rows.length === 0 ? (
            <p className="text-sm text-ink-muted">No {tab === "sessions" ? "sessions" : "students"} match these filters.</p>
          ) : tab === "sessions" ? (
            <>
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full min-w-[860px] border-collapse text-left">
                  <caption className="sr-only">
                    Attendance sessions with the backend status, present count over roster total, and late minutes.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className={TH_CLASS}>Date</th>
                      <th scope="col" className={TH_CLASS}>Period</th>
                      <th scope="col" className={TH_CLASS}>Course</th>
                      <th scope="col" className={TH_CLASS}>Section</th>
                      <th scope="col" className={TH_CLASS}>Status</th>
                      <th scope="col" className={TH_CLASS}>Present / total</th>
                      <th scope="col" className={TH_CLASS}>Late</th>
                      <th scope="col" className={TH_CLASS}><span className="sr-only">Roster</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const open = selectedRow?.key === row.key;
                      return (
                        <tr
                          key={row.key}
                          className={`${open ? "bg-primary-soft" : ""} ${row.occurrenceId != null ? "cursor-pointer" : ""}`}
                          onClick={row.occurrenceId != null ? () => setSelected(open ? null : row) : undefined}
                        >
                          <td className={`${TD_CLASS} whitespace-nowrap`}>{formatDate(row.date)}</td>
                          <td className={TD_CLASS}>{row.period || "-"}</td>
                          <td className={`${TD_CLASS} font-semibold`}>{row.courseCode || "-"}</td>
                          <td className={TD_CLASS}>{row.section || "-"}</td>
                          <td className={TD_CLASS}>
                            <StatusChip session={row} />
                            {row.status === "HOLIDAY" && closureReason(row) ? (
                              <p className="mt-1 text-xs text-ink-muted">{closureReason(row)}</p>
                            ) : null}
                          </td>
                          <td className={`${TD_CLASS} whitespace-nowrap`}>
                            {row.present ?? "-"}/{row.total ?? "-"}
                          </td>
                          <td className={`${TD_CLASS} whitespace-nowrap`}>
                            {row.status === "CONDUCTED_LATE" && row.lateMinutes != null ? `${row.lateMinutes}m` : "-"}
                          </td>
                          <td className={`${TD_CLASS} whitespace-nowrap text-right`}>
                            {row.occurrenceId != null ? (
                              <Button
                                variant="secondary"
                                className="px-3 py-1.5 text-xs"
                                aria-expanded={open}
                                aria-controls={open ? "attendance-roster" : undefined}
                                aria-label={`${open ? "Hide" : "Show"} roster for ${row.courseCode || "this course"} on ${formatDate(row.date)} ${row.period || ""}`}
                                onClick={() => setSelected(open ? null : row)}
                              >
                                {open ? "Hide roster" : "Roster"}
                              </Button>
                            ) : (
                              <span className="text-xs text-ink-muted" title="This row comes from an audit snapshot and has no occurrence record to list.">
                                No record
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {selectedRow ? (
                <div id="attendance-roster" className="mt-5 rounded-lg border border-line bg-surface-muted p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-bold text-ink">
                        Roster - {formatDate(selectedRow.date)} - {selectedRow.period} - {selectedRow.courseCode}
                      </h3>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        {selectedRow.section || "No section"} - {selectedRow.present ?? "-"}/{selectedRow.total ?? "-"} present - times in {timezone}
                      </p>
                    </div>
                    <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setSelected(null)}>
                      Close roster
                    </Button>
                  </div>

                  {rosterError ? (
                    <StatusBanner
                      tone="error"
                      title={rosterError.code === "out_of_scope" ? "This roster is outside the current filters" : "Couldn't load this roster"}
                      className="mt-3"
                    >
                      {describeApiError(rosterError)}
                      <Button variant="secondary" className="mt-2" onClick={() => setRosterAttempt((value) => value + 1)}>
                        Retry
                      </Button>
                    </StatusBanner>
                  ) : rosterLoading ? (
                    <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-muted">
                      <Spinner /> Loading the roster...
                    </p>
                  ) : !rosterData || rosterData.rows.length === 0 ? (
                    <p className="mt-3 text-sm text-ink-muted">No check-ins were recorded for this session.</p>
                  ) : (
                    <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface">
                      <table className="w-full min-w-[720px] border-collapse text-left">
                        <caption className="sr-only">Check-ins for the selected session, including method and device verification.</caption>
                        <thead>
                          <tr>
                            <th scope="col" className={TH_CLASS}>Name</th>
                            <th scope="col" className={TH_CLASS}>Roll number</th>
                            <th scope="col" className={TH_CLASS}>Timestamp</th>
                            <th scope="col" className={TH_CLASS}>Method</th>
                            <th scope="col" className={TH_CLASS}>Device verified</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rosterData.rows.map((entry) => (
                            <tr key={`${entry.studentId}:${entry.sessionId}`}>
                              <td className={TD_CLASS}>{entry.name || "-"}</td>
                              <td className={TD_CLASS}>{entry.rollNumber || "-"}</td>
                              <td className={`${TD_CLASS} whitespace-nowrap`}>{formatStamp(entry.timestamp, timezone)}</td>
                              <td className={TD_CLASS}>{entry.method || "-"}</td>
                              <td className={TD_CLASS}>
                                {entry.deviceVerified === true ? (
                                  <span className={`${CHIP} bg-success-soft text-success`}>Verified</span>
                                ) : entry.deviceVerified === false ? (
                                  <span className={`${CHIP} bg-danger-soft text-danger-strong`}>Not verified</span>
                                ) : (
                                  <span className="text-ink-muted">-</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div
                className="max-h-[34rem] overflow-auto rounded-lg border border-line focus-visible:outline-2 focus-visible:outline-primary"
                tabIndex={0}
                role="region"
                aria-label="Attendance matrix, scroll vertically and horizontally"
              >
                <table className="min-w-max border-separate border-spacing-0 text-left">
                  <caption className="sr-only">
                    One row per student, one column per returned session. Cells show present, absent, or not conducted. The percentage is the backend's value.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className={`${TH_CLASS} sticky left-0 top-0 z-20 bg-surface-muted`}>Student</th>
                      {(panelData.sessions || []).map((session) => (
                        <th
                          key={session.key}
                          scope="col"
                          title={`${formatDate(session.date)} - ${session.period} - ${session.courseCode} - ${statusLabel(session)}`}
                          className={`${TH_CENTER} sticky top-0 z-10 whitespace-nowrap`}
                        >
                          <span className="block">{shortDate(session.date)}</span>
                          <span className="block text-[10px] font-normal normal-case tracking-normal text-ink-muted">
                            {session.period} - {session.courseCode}
                          </span>
                        </th>
                      ))}
                      <th scope="col" className={`${TH_CENTER} sticky top-0 z-10`}>Present</th>
                      <th scope="col" className={`${TH_CENTER} sticky top-0 z-10`}>Conducted</th>
                      <th scope="col" className={`${TH_CENTER} sticky top-0 z-10`}>Scheduled</th>
                      <th scope="col" className={`${TH_CENTER} sticky top-0 z-10`}>%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const cells = new Map(row.cells.map((cell) => [cell.sessionKey, cell]));
                      const notConducted = row.scheduled - row.conducted;
                      // The backend returns null when nothing was conducted; the
                      // conducted guard keeps the dash if a payload ever pairs a
                      // zero conducted count with a numeric percentage.
                      const hasPercentage = row.conducted > 0 && row.percentage != null;
                      return (
                        <tr key={row.studentId}>
                          <th scope="row" className={`${TD_CLASS} sticky left-0 z-10 bg-surface text-left`}>
                            <span className="block font-semibold">{row.name || "-"}</span>
                            <span className="block text-xs text-ink-muted">{row.rollNumber || "-"} - {row.section || "No section"}</span>
                            {row.conducted < row.scheduled ? (
                              <span className="mt-1 block text-xs font-semibold text-warning">
                                {notConducted} {notConducted === 1 ? "class" : "classes"} not conducted by instructor
                              </span>
                            ) : null}
                          </th>
                          {(panelData.sessions || []).map((session) => (
                            <MatrixCell key={session.key} cell={cells.get(session.key)} session={session} />
                          ))}
                          <td className={`${TD_CLASS} text-center font-semibold`}>{row.present}</td>
                          <td className={`${TD_CLASS} text-center font-semibold`}>{row.conducted}</td>
                          <td className={`${TD_CLASS} text-center`}>{row.scheduled}</td>
                          <td className={`${TD_CLASS} text-center ${
                            !hasPercentage
                              ? "text-ink-muted"
                              : row.percentage < 75
                                ? "bg-danger-soft"
                                : ""
                          }`}>
                            <span className={`font-bold ${
                              !hasPercentage
                                ? "text-ink-muted"
                                : row.percentage < 75
                                  ? "text-danger-strong"
                                  : "text-success"
                            }`}>
                              {hasPercentage ? `${row.percentage}%` : "\u2014"}
                            </span>
                            <span className="sr-only">
                              {!hasPercentage
                                ? "No percentage: no classes were conducted."
                                : row.percentage < 75 ? "Below the 75 percent threshold." : ""}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs text-ink-muted">
                {panelData.total > rows.length
                  ? `Showing the first ${rows.length} of ${panelData.total} students; the API returns at most 500 per matrix request. `
                  : ""}
                Below 75% is highlighted. A dash means the backend returned no percentage because no classes were conducted.
              </p>
            </>
          )}
        </Card>

        <Card
          title="Downloads and verification"
          description="Both use the filters above. The download keeps the server's header block, which names the term, timezone, requester and every filter applied."
        >
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={() => download("csv")}
              loading={downloadState.busy === "csv"}
              disabled={!canQuery || (Boolean(downloadState.busy) && downloadState.busy !== "csv")}
            >
              Download CSV
            </Button>
            <Button
              variant="secondary"
              onClick={() => download("xlsx")}
              loading={downloadState.busy === "xlsx"}
              disabled={!canQuery || (Boolean(downloadState.busy) && downloadState.busy !== "xlsx")}
            >
              Download Excel
            </Button>
            <Button variant="secondary" onClick={verify} loading={verifyState.busy} disabled={!canQuery || panelLoading}>
              Verify
            </Button>
          </div>

          {!canQuery ? (
            <p className="mt-3 text-xs text-ink-muted">Choose a complete filter set before downloading or verifying.</p>
          ) : (
            <p className="mt-3 text-xs text-ink-muted">
              Verification compares the database with AttendanceLedger for the first 500 students returned by these filters.
            </p>
          )}

          {downloadState.error ? (
            <StatusBanner tone="error" title="Couldn't download the report" className="mt-3">
              {describeApiError(downloadState.error)}
            </StatusBanner>
          ) : null}
          {downloadState.saved ? (
            <StatusBanner tone="success" title="Download ready" className="mt-3">
              Saved {downloadState.saved} with the filters currently applied.
            </StatusBanner>
          ) : null}

          {verifyState.busy ? (
            <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-muted">
              <Spinner /> Checking AttendanceLedger...
            </p>
          ) : null}
          {verifyState.error ? (
            <StatusBanner tone="error" title="Couldn't verify attendance" className="mt-3">
              {describeApiError(verifyState.error)}
              <Button variant="secondary" className="mt-2" onClick={verify}>Retry</Button>
            </StatusBanner>
          ) : null}

          {verifyState.result && verifyState.result.mismatches.length === 0 ? (
            <StatusBanner tone="success" className="mt-3">
              Verified against AttendanceLedger - {verifyState.result.verified} entries match
              {verifyState.result.verified === 0 ? (
                <span className="mt-1 block text-xs">
                  No conducted entries were in scope for these filters, so nothing was compared.
                </span>
              ) : null}
            </StatusBanner>
          ) : null}

          {verifyState.result && verifyState.result.mismatches.length > 0 ? (
            <>
              <StatusBanner
                tone="error"
                title={`${verifyState.result.mismatches.length} mismatched ${verifyState.result.mismatches.length === 1 ? "entry" : "entries"} against AttendanceLedger`}
                className="mt-3"
              >
                The database and the ledger disagree for the sessions below. Attendance was not changed.
              </StatusBanner>
              <div className="mt-3 overflow-x-auto rounded-lg border border-line">
                <table className="w-full min-w-[760px] border-collapse text-left">
                  <caption className="sr-only">Sessions with mismatched attendance between the database and AttendanceLedger.</caption>
                  <thead>
                    <tr>
                      <th scope="col" className={TH_CLASS}>Session</th>
                      <th scope="col" className={TH_CLASS}>Period</th>
                      <th scope="col" className={TH_CLASS}>Course</th>
                      <th scope="col" className={TH_CLASS}>Section</th>
                      <th scope="col" className={TH_CLASS}>Mismatched entries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mismatchGroups.map((group) => (
                      <tr key={group.key}>
                        <td className={`${TD_CLASS} whitespace-nowrap`}>
                          {group.session ? formatDate(group.session.date) : `Occurrence #${group.key}`}
                        </td>
                        <td className={TD_CLASS}>{group.session?.period || "-"}</td>
                        <td className={TD_CLASS}>{group.session?.courseCode || "-"}</td>
                        <td className={TD_CLASS}>{group.session?.section || "-"}</td>
                        <td className={TD_CLASS}>
                          <p className="font-semibold">{group.entries.length}</p>
                          <ul className="mt-1 space-y-1 text-xs text-ink-muted">
                            {group.entries.map((entry) => (
                              <li key={`${entry.studentId}:${entry.sessionId}`}>
                                Student #{entry.studentId}: database {entry.databasePresent ? "present" : "absent"},
                                ledger {entry.ledgerPresent ? "present" : "absent"}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </Card>
      </div>
    </PageLayout>
  );
}
