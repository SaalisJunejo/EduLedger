import { Fragment, useEffect, useRef, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import Spinner from "../components/ui/Spinner.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";

const TABS = [
  { view: "class", label: "By class", picker: "Class" },
  { view: "teacher", label: "By teacher", picker: "Teacher" },
  { view: "room", label: "By classroom", picker: "Classroom" },
];
const COLOUR_BY = ["Course", "Section", "Room", "Type"];
const EMPTY_OPTIONS = [];
const PALETTE = {
  sky: "bg-sky-100", emerald: "bg-emerald-100", amber: "bg-amber-100",
  violet: "bg-violet-100", teal: "bg-teal-100", orange: "bg-orange-100",
  indigo: "bg-indigo-100", lime: "bg-lime-100", fuchsia: "bg-fuchsia-100", cyan: "bg-cyan-100",
};
const WARNING_LABELS = {
  teacher_clash: "Teacher clash", room_clash: "Room clash", section_clash: "Section clash",
  lab_room_mismatch: "Room-type warning",
};
const SELECT_STYLE = "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";

function weekPath(view, id, term) {
  return `/timetable?${new URLSearchParams({ view, id: String(id), term: String(term) })}`;
}

function screenSlots(week) {
  return (week?.days || []).flatMap((day) => day.cells.flatMap((cell) => (
    cell.kind === "SLOT" ? [cell, ...(cell.overlaps || [])] : []
  )));
}

// There is no staff-wide faculty directory. Derive scheduled teachers from
// authorized class weeks, limiting concurrent requests rather than paging.
async function classWeeks(offerings, term, isActive) {
  const weeks = new Map();
  let index = 0;
  async function worker() {
    while (isActive() && index < offerings.length) {
      const offering = offerings[index++];
      const week = await api(weekPath("class", offering.id, term));
      weeks.set(String(offering.id), week);
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, offerings.length) }, worker));
  return weeks;
}

async function loadCatalog(user, requestedTerm, isActive) {
  const query = requestedTerm ? `?${new URLSearchParams({ term: requestedTerm })}` : "";
  const response = await api(`/timetable/offerings${query}`);
  const offerings = response.offerings.map((entry) => ({ id: entry.id, label: entry.sectionLabel }));
  if (user.role === "FACULTY") {
    return {
      term: response.term, offerings, weeks: new Map(), rooms: [], notice: null,
      teachers: [{ id: user.id, label: user.fullName || user.email || `Teacher #${user.id}` }],
    };
  }
  const [weeks, directory] = await Promise.all([
    classWeeks(offerings, response.term.id, isActive),
    api("/rooms").catch((error) => ({ rooms: [], error })),
  ]);
  const teachers = new Map();
  const rooms = new Map(directory.rooms.map((room) => [room.id, { id: room.id, label: room.name }]));
  for (const week of weeks.values()) {
    for (const slot of screenSlots(week)) {
      teachers.set(slot.facultyId, { id: slot.facultyId, label: slot.facultyName });
      rooms.set(slot.roomId, { id: slot.roomId, label: slot.roomName });
    }
  }
  const sort = (items) => [...items.values()].sort((a, b) => a.label.localeCompare(b.label) || a.id - b.id);
  return {
    term: response.term, offerings, weeks, teachers: sort(teachers), rooms: sort(rooms),
    notice: directory.error ? `Room directory unavailable; showing rooms used in this term. ${directory.error.message}` : null,
  };
}

function category(entry, colourBy, legend) {
  if (colourBy === "type") return { colour: entry.isLab ? "violet" : "sky" };
  const collection = { course: "courses", section: "sections", room: "rooms" }[colourBy];
  return legend?.[collection]?.find((item) => item.id === entry[`${colourBy}Id`]);
}

function background(entry, colourBy, legend) {
  const token = category(entry, colourBy, legend)?.colour || (colourBy === "course" ? entry.colour : null);
  return PALETTE[token] || "bg-surface-muted";
}

function visibleLegend(week, colourBy) {
  const slots = screenSlots(week);
  if (colourBy === "type") {
    return [false, true].filter((isLab) => slots.some((slot) => slot.isLab === isLab)).map((isLab) => ({
      id: isLab ? "lab" : "lecture", label: isLab ? "LAB" : "LECTURE", colour: isLab ? "violet" : "sky",
    }));
  }
  const collection = { course: "courses", section: "sections", room: "rooms" }[colourBy];
  const ids = new Set(slots.map((slot) => slot[`${colourBy}Id`]));
  return (week?.legend?.[collection] || []).filter((item) => ids.has(item.id)).map((item) => ({
    ...item, label: colourBy === "course" ? `${item.code} · ${item.title}` : item.label || item.name,
  }));
}

function SlotBlock({ slot, view, colourBy, legend, warnings }) {
  const ids = slot.slotIds || [slot.slotId];
  const affected = warnings.filter((warning) => warning.slotIds.some((id) => ids.includes(id)));
  const labels = [...new Set(affected.map((warning) => WARNING_LABELS[warning.error] || "Warning"))];
  return (
    <div className={`h-full min-h-36 rounded-lg p-3 text-ink ${background(slot, colourBy, legend)} ${
      affected.length ? "border-4 border-double border-ink" : "border border-line"
    }`}>
      <p className="break-words text-sm font-extrabold">{slot.courseCode}</p>
      <p className="mt-1 break-words text-xs leading-relaxed">{slot.courseTitle}</p>
      {view !== "teacher" ? <p className="mt-2 break-words text-xs">Teacher: {slot.facultyName}</p> : null}
      {view !== "room" ? <p className="mt-1 break-words text-xs">Room: {slot.roomName}</p> : null}
      {view !== "class" ? <p className="mt-1 break-words text-xs">{slot.sectionLabel}</p> : null}
      <span className="mt-2 inline-flex rounded-full border border-ink/20 px-2 py-0.5 text-[10px] font-bold tracking-wide">
        {slot.isLab ? "LAB" : "LECTURE"}
      </span>
      {slot.isLab && slot.span === 2 ? (
        <p className="mt-1 text-xs font-bold">Lab (P{slot.period}-P{slot.period + 1})</p>
      ) : null}
      {labels.length ? <p className="mt-2 text-xs font-bold">{labels.join(" · ")}</p> : null}
    </div>
  );
}

function WeekGrid({ week, periods, view, colourBy }) {
  const teaching = periods.filter((period) => !period.isBreak).sort((a, b) => a.ordinal - b.ordinal);
  const breakPeriod = periods.find((period) => period.isBreak);
  return (
    <div className="overflow-x-auto rounded-lg border border-line focus-visible:outline-2 focus-visible:outline-primary"
      role="region" aria-label="Weekly timetable, scroll horizontally on smaller screens" tabIndex={0}>
      <table className="w-full min-w-[1120px] table-fixed border-separate border-spacing-0 text-left">
        <caption className="sr-only">Monday through Friday, six teaching periods with a break after P3. Lab pairs occupy one merged cell.</caption>
        <colgroup>
          <col className="w-20" />
          {teaching.map((period) => (
            <Fragment key={period.id}>
              <col />
              {period.ordinal === 3 ? <col className="w-16" /> : null}
            </Fragment>
          ))}
        </colgroup>
        <thead>
          <tr className="bg-surface-muted">
            <th scope="col" className="border-b border-line p-3 text-xs font-bold">Day</th>
            {teaching.map((period) => (
              <Fragment key={period.id}>
                <th scope="col" className="border-b border-l border-line p-3">
                  <span className="block text-sm font-bold">P{period.ordinal}</span>
                  <span className="mt-1 block text-xs font-normal text-ink-muted">{period.startTime}–{period.endTime}</span>
                </th>
                {period.ordinal === 3 ? (
                  <th scope="col" className="border-x border-b border-line px-1 py-3 text-center">
                    <span className="block text-xs font-bold">Break</span>
                    <span className="mt-1 block text-[10px] font-normal text-ink-muted">{breakPeriod.startTime}<br />{breakPeriod.endTime}</span>
                  </th>
                ) : null}
              </Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {week.days.map((day, dayIndex) => (
            <tr key={day.dayOfWeek}>
              <th scope="row" className="border-b border-line bg-surface-muted p-3 align-top text-sm font-bold">
                {["Mon", "Tue", "Wed", "Thu", "Fri"][day.dayOfWeek]}
              </th>
              {day.cells.map((cell) => {
                // The API already merged the lab. Its COVERED marker is not another cell.
                if (cell.kind === "COVERED") return null;
                const span = cell.kind === "SLOT" ? cell.span : 1;
                return (
                  <Fragment key={cell.period}>
                    <td colSpan={span} className="border-b border-l border-line p-1 align-top">
                      {cell.kind === "SLOT" ? (
                        <div className="flex h-full flex-col gap-1">
                          {[cell, ...(cell.overlaps || [])].map((slot) => (
                            <SlotBlock key={slot.slotId} slot={slot} view={view} colourBy={colourBy}
                              legend={week.legend} warnings={week.warnings} />
                          ))}
                        </div>
                      ) : (
                        <div className="min-h-36 rounded-lg bg-surface-muted" aria-label={`P${cell.period}: empty`}>
                          <span className="sr-only">Empty</span>
                        </div>
                      )}
                    </td>
                    {cell.period + span - 1 === 3 && dayIndex === 0 ? (
                      <td rowSpan={week.days.length} className="border-x border-b border-line bg-surface-muted text-center align-middle">
                        <span className="text-xs font-semibold text-ink-muted [writing-mode:vertical-rl]">Break · {breakPeriod.startTime}–{breakPeriod.endTime}</span>
                      </td>
                    ) : null}
                  </Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Staff-only, read-only weekly pattern. View, target, term and colour choices are shareable. */
export default function Timetable() {
  const user = useOutletContext();
  const [params, setParams] = useSearchParams();
  const defaultView = user.role === "FACULTY" ? "teacher" : "class";
  const view = TABS.some((tab) => tab.view === params.get("view")) ? params.get("view") : defaultView;
  const tab = TABS.find((entry) => entry.view === view);
  const targetId = params.get("id") || "";
  const requestedTerm = params.get("term") || "";
  const colourKey = `colour_${view}`;
  const rawColour = params.get(colourKey) || "course";
  const colourBy = COLOUR_BY.some((label) => label.toLowerCase() === rawColour) ? rawColour : "course";
  const roomRestricted = user.role === "FACULTY" && view === "room";
  const catalogKey = `${user.id}:${requestedTerm || "active"}`;
  const catalogCache = useRef(new Map());
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [catalogState, setCatalogState] = useState({ key: null, data: null, error: null });
  const [periodAttempt, setPeriodAttempt] = useState(0);
  const [periodState, setPeriodState] = useState({ rows: null, error: null });
  const [gridAttempt, setGridAttempt] = useState(0);
  const [gridState, setGridState] = useState({ key: null, data: null, error: null });

  useEffect(() => {
    let active = true;
    setPeriodState({ rows: null, error: null });
    api("/periods").then((response) => {
      if (active) setPeriodState({ rows: response.periods, error: null });
    }).catch((error) => {
      if (active) setPeriodState({ rows: null, error });
    });
    return () => { active = false; };
  }, [periodAttempt]);

  useEffect(() => {
    const cached = catalogCache.current.get(catalogKey);
    if (cached && catalogAttempt === 0) {
      setCatalogState({ key: catalogKey, data: cached, error: null });
      return;
    }
    let active = true;
    setCatalogState({ key: catalogKey, data: null, error: null });
    loadCatalog(user, requestedTerm, () => active).then((data) => {
      if (!active) return;
      catalogCache.current.set(catalogKey, data);
      catalogCache.current.set(`${user.id}:${data.term.id}`, data);
      setCatalogState({ key: catalogKey, data, error: null });
    }).catch((error) => {
      if (active) setCatalogState({ key: catalogKey, data: null, error });
    });
    return () => { active = false; };
  }, [catalogKey, requestedTerm, user, catalogAttempt]);

  const catalog = catalogState.key === catalogKey ? catalogState.data : null;
  const catalogError = catalogState.key === catalogKey ? catalogState.error : null;
  const options = (view === "class" ? catalog?.offerings : view === "teacher" ? catalog?.teachers : catalog?.rooms) || EMPTY_OPTIONS;

  useEffect(() => {
    if (!catalog) return;
    const next = new URLSearchParams(params);
    next.set("view", view);
    if (!requestedTerm) next.set("term", String(catalog.term.id));
    if (!targetId && options.length && !roomRestricted) next.set("id", String(options[0].id));
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [catalog, params, view, requestedTerm, targetId, options, roomRestricted, setParams]);

  const gridKey = `${catalogKey}:${view}:${targetId}`;
  useEffect(() => {
    if (!catalog || !targetId || roomRestricted) return;
    let active = true;
    const cached = view === "class" && gridAttempt === 0 ? catalog.weeks.get(targetId) : null;
    setGridState({ key: gridKey, data: cached || null, error: null });
    if (!cached) {
      api(weekPath(view, targetId, catalog.term.id)).then((data) => {
        if (active) setGridState({ key: gridKey, data, error: null });
      }).catch((error) => {
        if (active) setGridState({ key: gridKey, data: null, error });
      });
    }
    return () => { active = false; };
  }, [catalog, targetId, roomRestricted, view, gridKey, gridAttempt]);

  const week = catalog && !roomRestricted && gridState.key === gridKey ? gridState.data : null;
  const gridError = catalog && !roomRestricted && gridState.key === gridKey ? gridState.error : null;
  const periods = periodState.rows || [];
  const periodsComplete = [1, 2, 3, 4, 5, 6].every((ordinal) => periods.some((period) => !period.isBreak && period.ordinal === ordinal))
    && periods.some((period) => period.isBreak);
  const legend = visibleLegend(periodsComplete ? week : null, colourBy);
  const sharedTarget = targetId && !options.some((option) => String(option.id) === targetId);
  const sharedLabel = week?.target?.sectionLabel || week?.target?.fullName || week?.target?.name || `Shared ${tab.picker.toLowerCase()} #${targetId}`;
  const warnings = week?.warnings || [];

  function changeView(nextView) {
    if (nextView === view) return;
    const next = new URLSearchParams(params);
    next.set("view", nextView);
    next.delete("id");
    if (catalog) next.set("term", String(catalog.term.id));
    setParams(next);
  }

  function changeParam(key, value) {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (catalog) next.set("term", String(catalog.term.id));
    setParams(next);
  }

  function tabKeyDown(event, index) {
    const moves = { ArrowRight: (index + 1) % TABS.length, ArrowLeft: (index + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = TABS[moves[event.key]];
    changeView(next.view);
    document.getElementById(`timetable-tab-${next.view}`)?.focus();
  }

  return (
    <PageLayout role={user.role} title="Timetable" wide
      description="Read-only weekly teaching pattern. Share this page's URL to reopen the same view, selection and term. Times are shown in Asia/Karachi.">
      <Card>
        <div role="tablist" aria-label="Timetable views" className="flex flex-wrap gap-2">
          {TABS.map((entry, index) => (
            <Button key={entry.view} id={`timetable-tab-${entry.view}`} role="tab"
              aria-selected={view === entry.view} aria-controls="timetable-panel" tabIndex={view === entry.view ? 0 : -1}
              variant={view === entry.view ? "primary" : "secondary"}
              onClick={() => changeView(entry.view)} onKeyDown={(event) => tabKeyDown(event, index)}>
              {entry.label}
            </Button>
          ))}
        </div>
        <div id="timetable-panel" role="tabpanel" aria-labelledby={`timetable-tab-${view}`} className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_12rem]">
            <label className="block text-xs font-bold uppercase tracking-wide text-ink-muted" htmlFor="timetable-picker">
              {tab.picker}
              <select id="timetable-picker" value={targetId} className={`mt-2 ${SELECT_STYLE}`}
                disabled={!catalog || roomRestricted || (!options.length && !sharedTarget)}
                onChange={(event) => changeParam("id", event.target.value)}>
                {!targetId ? <option value="">{!catalog ? "Loading choices…" : roomRestricted ? "Not available for faculty" : options.length ? `Choose a ${tab.picker.toLowerCase()}` : `No ${tab.picker.toLowerCase()} choices in this term`}</option> : null}
                {sharedTarget ? <option value={targetId}>{sharedLabel}</option> : null}
                {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label>
            <label className="block text-xs font-bold uppercase tracking-wide text-ink-muted" htmlFor="timetable-colour">
              Colour by
              <select id="timetable-colour" className={`mt-2 ${SELECT_STYLE}`} value={colourBy}
                onChange={(event) => changeParam(colourKey, event.target.value)}>
                {COLOUR_BY.map((label) => <option key={label} value={label.toLowerCase()}>{label}</option>)}
              </select>
            </label>
          </div>
          {catalog ? <p className="text-xs text-ink-muted">Term: {catalog.term.name} · {catalog.term.startDate}–{catalog.term.endDate}</p> : null}
          {!catalog && !catalogError ? <p role="status" className="flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Loading timetable choices…</p> : null}
          {catalogError ? (
            <StatusBanner tone="error" title="Couldn't load timetable choices">
              {catalogError.message}
              <Button variant="secondary" className="mt-2" onClick={() => setCatalogAttempt((value) => value + 1)}>Retry</Button>
            </StatusBanner>
          ) : null}
          {catalog?.notice ? <StatusBanner tone="info">{catalog.notice}</StatusBanner> : null}
          {roomRestricted ? (
            <StatusBanner tone="info" title="Classroom view is restricted">
              HOD and ADMIN can read classroom timetables. Faculty can read their own teacher view and the classes they teach.
            </StatusBanner>
          ) : null}
        </div>
      </Card>

      <Card title="Weekly timetable">
        <section aria-labelledby="timetable-legend" className="mb-4">
          <h3 id="timetable-legend" className="text-xs font-bold uppercase tracking-wide text-ink-muted">Legend · {colourBy}</h3>
          {legend.length ? (
            <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
              {legend.map((item) => (
                <li key={item.id} className="flex items-center gap-2 text-xs text-ink">
                  <span aria-hidden="true" className={`h-4 w-4 shrink-0 rounded border border-line ${PALETTE[item.colour] || "bg-surface-muted"}`} />
                  {item.label}
                </li>
              ))}
            </ul>
          ) : <p className="mt-2 text-xs text-ink-muted">No scheduled items on screen.</p>}
          <p className="mt-2 text-xs text-ink-muted">Course codes always identify classes. Double borders mark clashes or room-type warnings, never colour categories.</p>
        </section>
        {warnings.length ? (
          <p role="status" className="mb-4 rounded-lg border-4 border-double border-ink bg-surface-muted px-3 py-2 text-sm text-ink">
            {warnings.length} timetable warning{warnings.length === 1 ? "" : "s"}. Outlined blocks are affected; term-wide warnings may involve other views.
          </p>
        ) : null}
        {periodState.error ? (
          <StatusBanner tone="error" title="Couldn't load period times">
            {periodState.error.message}
            <Button variant="secondary" className="mt-2" onClick={() => setPeriodAttempt((value) => value + 1)}>Retry</Button>
          </StatusBanner>
        ) : !periodState.rows ? (
          <p role="status" className="mb-3 flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Loading period times…</p>
        ) : !periodsComplete ? (
          <StatusBanner tone="error" title="Period grid is incomplete">An administrator must seed all six teaching periods and the break before the grid can be displayed.</StatusBanner>
        ) : null}
        {gridError ? (
          <StatusBanner tone="error" title={gridError.code === "out_of_scope" ? "This timetable is outside your access" : "Couldn't load this timetable"}>
            {gridError.message}
            <Button variant="secondary" className="mt-2" onClick={() => setGridAttempt((value) => value + 1)}>Retry</Button>
          </StatusBanner>
        ) : catalog && targetId && !roomRestricted && !week ? (
          <p role="status" className="mt-3 flex items-center gap-2 text-sm text-ink-muted"><Spinner /> Loading the complete week…</p>
        ) : null}
        {week && periodsComplete ? <WeekGrid week={week} periods={periods} view={view} colourBy={colourBy} /> : null}
      </Card>
    </PageLayout>
  );
}
