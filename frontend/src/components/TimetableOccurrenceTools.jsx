import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Button from "./ui/Button.jsx";
import Card from "./ui/Card.jsx";
import Spinner from "./ui/Spinner.jsx";
import StatusBanner from "./ui/StatusBanner.jsx";
import { api } from "../lib/api.js";

const INPUT = "mt-2 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL = "block text-xs font-bold uppercase tracking-wide text-ink-muted";
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const ZONE = "Asia/Karachi";
const WARNINGS = {
  section_clash: "Section clash: another class occupies this destination.",
  lab_room_mismatch: "The room type does not match the course type.",
  lab_group_split: "Only this half of the lab moved. The paired occurrence was not changed.",
};

export function timetableToday() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date()).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function dateObject(day) { return new Date(`${day}T00:00:00Z`); }
export function shiftDate(day, offset) {
  const date = dateObject(day);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}
export function weekMonday(day) {
  const date = dateObject(day);
  if (Number.isNaN(date.getTime())) return weekMonday(timetableToday());
  return shiftDate(day, -((date.getUTCDay() + 6) % 7));
}
function localClock(instant) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(instant));
}
function overlaps(start, end, otherStart, otherEnd) {
  return start < otherEnd && end > otherStart;
}
function rowPeriod(row, periods) {
  if (row.period && /^P[1-6]$/.test(row.period)) return Number(row.period.slice(1));
  return periods.find((period) => !period.isBreak && period.startTime === localClock(row.startTime))?.ordinal;
}
function weekUrl(view, id, term) {
  return `/timetable?${new URLSearchParams({ view, id: String(id), term: String(term) })}`;
}

// Expand merged pattern blocks only for metadata lookup. The pattern itself is
// never edited, and each dated half of a lab retains its own occurrence ID.
function patternSlots(week) {
  return (week.days || []).flatMap((day) => day.cells.flatMap((cell) => {
    if (cell.kind !== "SLOT") return [];
    return [cell, ...(cell.overlaps || [])].flatMap((slot) => (slot.slotIds || [slot.slotId]).map((id, index) => ({
      ...slot, slotId: id, dayOfWeek: day.dayOfWeek, period: cell.period + index,
      facultyName: slot.facultyName || (week.view === "teacher" ? week.target.fullName : null),
      roomName: slot.roomName || (week.view === "room" ? week.target.name : null),
      sectionLabel: slot.sectionLabel || (week.view === "class" ? week.target.sectionLabel : null),
    })));
  }));
}

async function datedRows(user, term, from, to) {
  const range = new URLSearchParams({ date_from: from, date_to: to });
  if (user.role === "FACULTY") {
    // Phase 2.5's faculty list supplies actual rooms, but not states/slot IDs.
    // The existing report supplies those fields and virtual holiday/cancel rows.
    const [classes, report] = await Promise.all([
      api("/faculty/classes"),
      api(`/faculty/sessions?${new URLSearchParams({ term: String(term), ...Object.fromEntries(range), course: "all", status: "ALL" })}`),
    ]);
    const actual = new Map(classes.classes.map((row) => [row.id, row]));
    return { rows: report.rows.map((row) => ({
      ...row, id: row.occurrenceId, state: row.status,
      professorId: row.facultyId, professorName: user.fullName || user.email,
      room: actual.get(row.occurrenceId)?.room || null,
    })), capped: false };
  }
  const response = await api(`/timetable/classes?${range}`);
  return {
    rows: [...response.classes, ...response.holidays, ...response.cancelled]
      .filter((row) => row.termId === term && row.date >= from && row.date <= to),
    // This endpoint caps real rows before date/term filtering. Never claim the
    // visible dataset is complete when the cap could have been reached.
    capped: response.count >= response.limit,
  };
}

export function HolidayTools({ user, onChanged }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ from: "", to: "", reason: "", scope: "UNIVERSITY", department: "" });
  const [state, setState] = useState({ busy: false, result: null, error: null });
  const pending = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  if (!["HOD", "ADMIN"].includes(user.role)) return null;
  const departmentScope = user.role === "HOD" || form.scope === "DEPARTMENT";
  const valid = form.from && form.to && form.to >= form.from && form.reason.trim().length >= 3
    && (!departmentScope || Boolean((user.role === "HOD" ? user.department : form.department)?.trim()));
  function change(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setState((prev) => ({ ...prev, error: null, result: null }));
  }
  async function submit(event) {
    event.preventDefault();
    if (!valid || state.result || pending.current) return;
    pending.current = true;
    setState({ busy: true, result: null, error: null });
    const body = { date_from: form.from, date_to: form.to, reason: form.reason.trim(),
      scope: departmentScope ? "DEPARTMENT" : "UNIVERSITY" };
    if (user.role === "ADMIN" && departmentScope) body.department = form.department.trim();
    try {
      const result = await api("/closures", { method: "POST", body });
      if (![result?.removed, result?.kept_started, result?.kept_logged].every((value) => Number.isInteger(value) && value >= 0)) {
        throw new Error("The closure response did not include the expected counts. Refresh before submitting again; the closure may have been saved.");
      }
      onChanged();
      if (!mounted.current) return;
      setState({ busy: false, result, error: null });
    } catch (error) {
      if (mounted.current) setState({ busy: false, result: null, error });
    } finally { pending.current = false; }
  }
  return (
    <Card>
      <Button variant="secondary" aria-expanded={open} aria-controls="holiday-form" disabled={state.busy}
        onClick={() => setOpen((value) => !value)}>Mark holiday</Button>
      {open ? (
        <form id="holiday-form" onSubmit={submit} className="mt-4 space-y-4">
          <StatusBanner tone="warning" title="Holidays skip classes; they never shift them">
            Submitting deletes future unstarted classes in this scope. Started occurrences and immutable history are kept.
            No classes move to another date, and the repeating timetable pattern is unchanged.
          </StatusBanner>
          <fieldset disabled={state.busy} className="space-y-4">
            <legend className="sr-only">Holiday dates and scope</legend>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className={LABEL}>Date from<input type="date" required className={INPUT} value={form.from} onChange={(event) => change("from", event.target.value)} /></label>
              <label className={LABEL}>Date to<input type="date" required min={form.from || undefined} className={INPUT} value={form.to} onChange={(event) => change("to", event.target.value)} /></label>
            </div>
            {user.role === "HOD" ? <p className="text-sm text-ink-muted">Scope: your department ({user.department || "not assigned"}).</p> : (
              <div className="grid gap-4 sm:grid-cols-2">
                <label className={LABEL}>Scope<select className={INPUT} value={form.scope} onChange={(event) => change("scope", event.target.value)}>
                  <option value="UNIVERSITY">Entire university</option><option value="DEPARTMENT">One department</option>
                </select></label>
                {departmentScope ? <label className={LABEL}>Department<input required maxLength={128} className={INPUT} value={form.department} onChange={(event) => change("department", event.target.value)} /></label> : null}
              </div>
            )}
            <label className={LABEL}>Reason<input required minLength={3} className={INPUT} value={form.reason} aria-describedby="holiday-reason-help" onChange={(event) => change("reason", event.target.value)} /></label>
            <p id="holiday-reason-help" className="text-xs text-ink-muted">The current closure API requires a reason of at least 3 non-whitespace characters; a blank optional reason is not accepted.</p>
          </fieldset>
          {state.error ? <StatusBanner tone="error" title="Couldn't mark holiday">{state.error.message}</StatusBanner> : null}
          {state.result ? <StatusBanner tone="success" title="Holiday marked">
            {state.result.removed} future unstarted occurrences removed; {state.result.kept_started} started occurrences kept; {state.result.kept_logged} occurrences with immutable history kept.
            No dates shifted. The repeating timetable pattern is unchanged.
          </StatusBanner> : null}
          <Button type="submit" variant="danger" loading={state.busy} disabled={!valid || Boolean(state.result)}>Confirm mark holiday</Button>
        </form>
      ) : null}
    </Card>
  );
}

function MoveChoices({ row, user, term, periods, snapshot, onSubmit, busy }) {
  const [attempt, setAttempt] = useState(0);
  const [source, setSource] = useState({ data: null, error: null });
  const [roomId, setRoomId] = useState("");
  const [roomAttempt, setRoomAttempt] = useState(0);
  const [roomWeek, setRoomWeek] = useState({ key: null, data: null, error: null });
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const first = weekMonday(row.date);
  useEffect(() => {
    let active = true;
    setSource({ data: null, error: null });
    setRoomId("");
    setChoice("");
    Promise.all([
      api(weekUrl("teacher", row.professorId, term.id)),
      user.role === "HOD" ? api("/rooms") : Promise.resolve(null),
    ]).then(([teacher, directory]) => {
      if (!active) return;
      const rooms = directory ? directory.rooms : teacher.legend.rooms;
      setSource({ data: { teacher, rooms }, error: null });
      setRoomId(String(rooms.find((room) => room.name === row.room)?.id || ""));
    }).catch((error) => {
      if (active) setSource({ data: null, error });
    });
    return () => { active = false; };
  }, [row.id, row.professorId, row.room, user.role, term.id, attempt]);

  useEffect(() => {
    if (!roomId || user.role !== "HOD") return undefined;
    let active = true;
    setRoomWeek({ key: roomId, data: null, error: null });
    api(weekUrl("room", roomId, term.id)).then((data) => {
      if (active) setRoomWeek({ key: roomId, data, error: null });
    }).catch((error) => {
      if (active) setRoomWeek({ key: roomId, data: null, error });
    });
    return () => { active = false; };
  }, [roomId, user.role, term.id, roomAttempt]);

  const roomData = roomWeek.key === roomId ? roomWeek.data : null;
  const roomError = roomWeek.key === roomId ? roomWeek.error : null;
  const room = source.data?.rooms.find((item) => String(item.id) === roomId);
  const loading = !source.data && !source.error;
  const roomLoading = user.role === "HOD" && roomId && !roomData && !roomError;
  const choices = useMemo(() => {
    if (!source.data || !room || roomLoading || roomError) return [];
    const teacherPattern = patternSlots(source.data.teacher);
    const roomPattern = roomData ? patternSlots(roomData) : [];
    const today = timetableToday();
    const options = [];
    for (let day = 0; day < 5; day += 1) {
      const date = shiftDate(first, day);
      if (date < today || date < term.startDate || date > term.endDate) continue;
      // Only backend-marked holidays block a day; do not invent closure state.
      if (snapshot.rows.some((other) => other.date === date && other.state === "HOLIDAY" && other.professorId === row.professorId)) continue;
      for (const period of periods.filter((item) => !item.isBreak)) {
        if (date === row.date && period.ordinal === rowPeriod(row, periods) && room.name === row.room) continue;
        const collidesWith = (other) => other.id !== row.id && other.date === date && !["CANCELLED", "HOLIDAY"].includes(other.state)
          && (other.professorId === row.professorId || other.room?.toLowerCase() === room.name.toLowerCase())
          && overlaps(period.startTime, period.endTime, localClock(other.startTime), other.endTime ? localClock(other.endTime) : "23:59");
        if (snapshot.rows.some(collidesWith)) continue;
        // Where dated rows already represent a pattern slot (including moved
        // and cancelled exceptions), use them instead of the repeating cell.
        const occupied = (slot) => slot.dayOfWeek === day && slot.period === period.ordinal && slot.slotId !== row.slotId
          && !snapshot.rows.some((other) => other.slotId === slot.slotId && other.date === date);
        if (teacherPattern.some(occupied) || roomPattern.some(occupied)) continue;
        options.push({ key: `${day}:${period.id}`, day, period, date });
      }
    }
    return options;
  }, [source.data, room, roomLoading, roomError, roomData, snapshot, row, periods, first, term.startDate, term.endDate]);
  const selected = choices.find((item) => item.key === choice);
  const valid = selected && room && reason.trim().length >= 3 && !snapshot.capped && !snapshot.unavailable;
  return (
    <form className="mt-4 space-y-4" onSubmit={(event) => {
      event.preventDefault();
      if (valid && !busy) onSubmit({ day_of_week: selected.day, period_id: selected.period.id, room_id: room.id, reason: reason.trim() });
    }}>
      <p className="text-sm text-ink-muted">Moves are limited by the existing API to Monday–Friday of this occurrence's week ({first} to {shiftDate(first, 4)}).</p>
      <fieldset disabled={busy} className="space-y-4">
        <legend className="sr-only">Move destination and reason</legend>
        {loading ? <p role="status" className="flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Loading move choices…</p> : null}
        {source.error ? <StatusBanner tone="error" title="Couldn't load move choices">{source.error.message}<Button variant="secondary" className="mt-2" onClick={() => setAttempt((value) => value + 1)}>Retry choices</Button></StatusBanner> : null}
        {source.data ? <>
          <label className={LABEL}>Room<select className={INPUT} value={roomId} disabled={!source.data.rooms.length} onChange={(event) => { setRoomId(event.target.value); setChoice(""); }}>
            <option value="">{source.data.rooms.length ? "Choose a room" : "No accessible rooms"}</option>
            {source.data.rooms.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          {roomLoading ? <p role="status" className="flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Checking room timetable…</p> : null}
          {roomError ? <StatusBanner tone="error" title="Couldn't check this room">{roomError.message}<Button variant="secondary" className="mt-2" onClick={() => setRoomAttempt((value) => value + 1)}>Retry room</Button></StatusBanner> : null}
          <label className={LABEL}>Free day and period in the visible timetable<select required className={INPUT} value={selected ? choice : ""} disabled={!choices.length} onChange={(event) => setChoice(event.target.value)}>
            <option value="">{!roomId ? "Choose a room first" : roomLoading ? "Checking availability…" : choices.length ? "Choose a destination" : "No available destinations in the accessible data"}</option>
            {choices.map((item) => <option key={item.key} value={item.key}>{DAYS[item.day]} {item.date} · P{item.period.ordinal} · {item.period.startTime}–{item.period.endTime}</option>)}
          </select></label>
          <p className="text-xs text-ink-muted">Choices exclude conflicts visible in the accessible pattern and dated data. The server checks all dated exceptions, closures and cross-term clashes before saving.
            {user.role === "FACULTY" ? " Faculty can select rooms used in their teacher pattern; other teachers' room bookings are not exposed to faculty." : " Room choices are limited to your department's room directory."}
          </p>
          {!source.data.rooms.length || (roomId && !roomLoading && !roomError && !choices.length) ? <StatusBanner tone="info">No move choices are available. You can still cancel this occurrence or close the panel.</StatusBanner> : null}
        </> : null}
        <label className={LABEL}>Move reason<input className={INPUT} required minLength={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <p className="text-xs text-ink-muted">At least 3 non-whitespace characters. Only this occurrence changes; the repeating pattern is untouched.</p>
      </fieldset>
      {snapshot.unavailable ? <StatusBanner tone="info">Moving is disabled until the dated data finishes refreshing successfully. If the refresh failed, use Retry occurrences above.</StatusBanner>
        : snapshot.capped ? <StatusBanner tone="warning">The dated listing reached its API limit. Availability cannot be checked reliably, so moving is disabled.</StatusBanner> : null}
      <Button type="submit" loading={busy} disabled={!valid}>Confirm move this occurrence</Button>
    </form>
  );
}

function OccurrenceDetail({ row: initial, user, week, periods, snapshot, onChanged, onClose }) {
  const [row, setRow] = useState(initial);
  const [action, setAction] = useState(null);
  const [reason, setReason] = useState("");
  const [state, setState] = useState({ busy: false, error: null, result: null, warnings: [] });
  const pending = useRef(false);
  const mounted = useRef(false);
  const heading = useRef(null);
  useEffect(() => {
    mounted.current = true;
    heading.current?.focus();
    return () => { mounted.current = false; };
  }, []);
  const canChange = row.id != null && !["HOLIDAY", "CANCELLED"].includes(row.state)
    && ((user.role === "FACULTY" && row.professorId === user.id)
      || (user.role === "HOD" && row.department && row.department === user.department));
  async function submit(kind, body) {
    if (!canChange || pending.current) return;
    pending.current = true;
    setState({ busy: true, error: null, result: null, warnings: [] });
    try {
      const response = await api(`/faculty/occurrences/${row.id}/${kind}`, { method: "POST", body });
      if (!response?.occurrence || response.occurrence.id !== row.id) {
        throw new Error("The response did not identify the expected occurrence. Refresh before retrying; the change may have been saved.");
      }
      onChanged();
      if (!mounted.current) return;
      setRow((prev) => ({ ...prev, ...response.occurrence, period: null }));
      setState({ busy: false, error: null, result: kind, warnings: response.warnings || [] });
      setAction(null);
    } catch (error) {
      // Includes session_already_started / occurrence_in_past. Do not close the
      // panel or lose the form just because the backend refused an operation.
      if (mounted.current) setState({ busy: false, error, result: null, warnings: [] });
    } finally { pending.current = false; }
  }
  return (
    <Card>
      <div role="region" aria-labelledby="occurrence-heading" onKeyDown={(event) => { if (event.key === "Escape" && !state.busy) onClose(); }}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="occurrence-heading" ref={heading} tabIndex={-1} className="text-base font-bold text-ink focus-visible:outline-2 focus-visible:outline-primary">Dated occurrence · {row.date}</h2>
          <Button variant="secondary" disabled={state.busy} onClick={onClose}>Close details</Button>
        </div>
        <p className="mt-2 text-sm text-ink-muted">These actions affect only this dated occurrence, not the repeating timetable pattern. The following week's pattern is unchanged.</p>
        <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-ink-muted">Course</dt><dd className="font-semibold text-ink">{row.courseCode}{row.courseTitle ? ` · ${row.courseTitle}` : ""}</dd></div>
          <div><dt className="text-ink-muted">Teacher</dt><dd className="text-ink">{row.professorName || `Teacher #${row.professorId}`}</dd></div>
          <div><dt className="text-ink-muted">Room</dt><dd className="text-ink">{row.room || "Not supplied for this virtual occurrence"}</dd></div>
          <div><dt className="text-ink-muted">Period</dt><dd className="text-ink">{rowPeriod(row, periods) ? `P${rowPeriod(row, periods)}` : "Outside the fixed period grid"} · {localClock(row.startTime)}{row.endTime ? `–${localClock(row.endTime)}` : ""} ({ZONE})</dd></div>
          <div><dt className="text-ink-muted">State</dt><dd className="text-ink">{row.state}</dd></div>
        </dl>
        {state.error ? <StatusBanner className="mt-4" tone="error" title={state.error.code || "Occurrence change failed"}>{state.error.message}</StatusBanner> : null}
        {state.result ? <StatusBanner className="mt-4" tone="success">{state.result === "cancel" ? "Occurrence cancelled." : `Occurrence moved to ${row.date}, ${row.room}.`} The repeating timetable pattern is unchanged.</StatusBanner> : null}
        {state.warnings.length ? <StatusBanner className="mt-4" tone="warning" title="Move warnings"><ul className="list-disc pl-4">{state.warnings.map((warning, index) => <li key={index}>{WARNINGS[warning.error] || warning.error}</li>)}</ul></StatusBanner> : null}
        {row.sessionGroupId ? <p className="mt-3 text-xs text-warning">This is one period of a paired lab. Moving it does not move its partner; the server reports a lab-group split.</p> : null}
        {canChange ? <>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="danger" disabled={state.busy} aria-pressed={action === "cancel"} onClick={() => { setAction("cancel"); setState((prev) => ({ ...prev, error: null, result: null })); }}>Cancel this occurrence</Button>
            <Button variant="secondary" disabled={state.busy} aria-pressed={action === "move"} onClick={() => { setAction("move"); setState((prev) => ({ ...prev, error: null, result: null })); }}>Move this occurrence</Button>
          </div>
          {action === "cancel" ? <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); if (reason.trim().length >= 3) submit("cancel", { reason: reason.trim() }); }}>
            <label className={LABEL}>Cancellation reason<input className={INPUT} required minLength={3} disabled={state.busy} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
            <p className="text-xs text-ink-muted">At least 3 non-whitespace characters. Only this dated occurrence will be cancelled.</p>
            <Button type="submit" variant="danger" loading={state.busy} disabled={reason.trim().length < 3}>Confirm cancel this occurrence</Button>
          </form> : null}
          {action === "move" ? <MoveChoices row={row} user={user} term={week.term} periods={periods} snapshot={snapshot} busy={state.busy} onSubmit={(body) => submit("move", body)} /> : null}
        </> : <p className="mt-4 text-sm text-ink-muted">{row.id == null ? "This is a virtual holiday/cancellation record, not an editable occurrence." : "Single-occurrence actions are available only to the owning faculty or the occurrence's department HOD, and not for holiday/cancelled records."}</p>}
      </div>
    </Card>
  );
}

export function DatedTimetable({ user, week, periods, from, offerings, metadataWeeks, onChanged, revision }) {
  const to = shiftDate(from, 4);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState({ data: null, error: null });
  const [selected, setSelected] = useState(null);
  const selectedRef = useRef(null);
  const gridRef = useRef(null);
  const slots = useMemo(() => patternSlots(week), [week]);
  const lookup = useMemo(() => new Map([
    ...[...metadataWeeks.values()].flatMap(patternSlots), ...slots,
  ].map((slot) => [slot.slotId, slot])), [metadataWeeks, slots]);
  const visibleIds = useMemo(() => new Set(slots.map((slot) => slot.slotId)), [slots]);
  const teaching = periods.filter((period) => !period.isBreak).sort((a, b) => a.ordinal - b.ordinal);
  const breakPeriod = periods.find((period) => period.isBreak);
  useEffect(() => {
    let active = true;
    setState({ data: null, error: null });
    datedRows(user, week.term.id, from, to).then((data) => {
      if (active) setState({ data, error: null });
    }).catch((error) => {
      if (active) setState({ data: null, error });
    });
    return () => { active = false; };
  }, [user.id, user.role, week.term.id, from, to, attempt, revision]);
  const rows = useMemo(() => (state.data?.rows || []).filter((row) => {
    if (week.view === "class") return visibleIds.has(row.slotId);
    if (week.view === "teacher") return row.professorId === week.target.id;
    return row.room === week.target.name;
  }).map((row) => {
    const slot = lookup.get(row.slotId);
    return { ...row,
      key: row.key || (row.id != null ? `occurrence:${row.id}` : `${row.state}:${row.slotId}:${row.date}:${row.originalOccurrenceId || "virtual"}`),
      courseTitle: slot?.courseTitle,
      professorName: row.professorName || slot?.facultyName,
      department: row.department || offerings.find((offering) => offering.id === slot?.offeringId)?.department,
    };
  }).sort((a, b) => a.startTime.localeCompare(b.startTime)), [state.data, week, lookup, visibleIds, offerings]);
  function openDetails(row, event) {
    selectedRef.current = event.currentTarget;
    setSelected(row);
  }
  function closeDetails() {
    setSelected(null);
    if (selectedRef.current?.isConnected) selectedRef.current.focus();
    else gridRef.current?.focus();
  }
  function occurrenceButton(row) {
    const muted = row.state === "HOLIDAY" || row.state === "CANCELLED";
    return <Button key={row.key} variant="secondary" aria-controls="dated-occurrence-details" aria-expanded={selected?.key === row.key}
      className={`w-full flex-col items-start text-left ${row.state === "HOLIDAY" ? "border-dashed bg-surface-muted! text-ink-muted!" : row.state === "CANCELLED" ? "border-double border-4 text-ink-muted!" : ""}`}
      onClick={(event) => openDetails(row, event)}>
      <span className={row.state === "CANCELLED" ? "line-through" : ""}>{row.courseCode}</span>
      <span className={`text-xs ${muted ? "text-ink-muted" : "text-ink"}`}>{row.state.replaceAll("_", " ")}</span>
      <span className="text-xs font-normal">{row.room || "Virtual record"}</span>
    </Button>;
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-muted">Dated week: {from}–{to}. Holiday = dashed muted border; cancelled = double muted border and struck-through course. These states are not palette categories. Each lab period is selectable separately.</p>
      {!state.data && !state.error ? <p role="status" className="flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Loading dated occurrences…</p> : null}
      {state.error ? <StatusBanner tone="error" title="Couldn't load dated occurrences">{state.error.message}<Button variant="secondary" className="mt-2" onClick={() => setAttempt((value) => value + 1)}>Retry occurrences</Button></StatusBanner> : null}
      {state.data?.capped ? <StatusBanner tone="warning">The API returns only the newest 500 real occurrences before filtering. This week may be incomplete; moving is disabled while the limit is reached.</StatusBanner> : null}
      {state.data && !rows.length ? <StatusBanner tone="info">No dated occurrences returned for this view and week. The repeating pattern may still contain classes; this page does not generate occurrences.</StatusBanner> : null}
      {state.data ? <div ref={gridRef} role="region" aria-label="Dated timetable week, scroll horizontally on smaller screens" tabIndex={0} className="overflow-x-auto rounded-lg border border-line focus-visible:outline-2 focus-visible:outline-primary">
        <table className="w-full min-w-[1120px] table-fixed border-separate border-spacing-0 text-left">
          <caption className="sr-only">Dated occurrences, Monday through Friday. Select a course to view its details.</caption>
          <thead><tr className="bg-surface-muted"><th scope="col" className="w-32 p-3 text-xs">Date</th>
            {teaching.map((period) => <Fragment key={period.id}><th scope="col" className="border-l border-line p-3 text-sm">P{period.ordinal}<span className="block text-xs font-normal text-ink-muted">{period.startTime}–{period.endTime}</span></th>{period.ordinal === 3 ? <th scope="col" className="w-16 border-x border-line p-1 text-xs">Break</th> : null}</Fragment>)}
          </tr></thead>
          <tbody>{DAYS.map((day, index) => {
            const date = shiftDate(from, index);
            return <tr key={date}><th scope="row" className="border-t border-line bg-surface-muted p-3 align-top text-sm">{day}<span className="mt-1 block text-xs font-normal">{date}</span></th>
              {teaching.map((period) => <Fragment key={period.id}><td className="border-l border-t border-line p-1 align-top"><div className="min-h-28 space-y-1">
                {rows.filter((row) => row.date === date && rowPeriod(row, periods) === period.ordinal).map(occurrenceButton)}
              </div></td>{period.ordinal === 3 && index === 0 ? <td rowSpan={5} className="border-x border-t border-line bg-surface-muted p-1 text-center text-xs text-ink-muted">{breakPeriod.startTime}<br />{breakPeriod.endTime}</td> : null}</Fragment>)}
            </tr>;
          })}</tbody>
        </table>
      </div> : null}
      {rows.some((row) => !rowPeriod(row, periods)) ? <section aria-label="Occurrences outside the fixed period grid" className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">Other dated occurrences</h3>
        {rows.filter((row) => !rowPeriod(row, periods)).map((row) => <div key={row.key}><p className="text-xs text-ink-muted">{row.date} · {localClock(row.startTime)}</p>{occurrenceButton(row)}</div>)}
      </section> : null}
      {selected ? <div id="dated-occurrence-details"><OccurrenceDetail key={selected.key} row={selected} user={user} week={week} periods={periods} snapshot={state.data || { rows: [], capped: false, unavailable: true }} onChanged={onChanged} onClose={closeDetails} /></div> : null}
    </div>
  );
}
