import { useRef, useState } from "react";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import { InstitutionExportCard } from "../components/ExportReportCard.jsx";
import PendingApprovalsCard from "../components/hod/PendingApprovalsCard.jsx";
import RecordHistoryCard from "../components/hod/RecordHistoryCard.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";
import { formatDateTime, professorLabel } from "../lib/workload.js";

// Dashboard sections as tabs on this route (no new routes — see App.jsx).
const TABS = [
  { id: "pending", label: "Pending Approvals" },
  { id: "history", label: "Record History" },
];

function TimetableUploadCard() {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  async function upload(event) {
    event.preventDefault();
    if (uploading) return;
    if (!file || !/\.(csv|xlsx)$/i.test(file.name)) {
      setError({ message: "Choose a .csv or .xlsx timetable file." });
      return;
    }
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const body = new FormData();
      body.append("file", file);
      // api() keeps the bearer token and lets the browser set the boundary.
      const data = await api("/hod/timetable/upload", { method: "POST", body });
      setResult(data);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (uploadError) {
      setError(uploadError);
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card title="Timetable upload" description="Import dated class occurrences from CSV or XLSX. Each upload creates new rows, not a weekly series or an update to previous uploads.">
      <form onSubmit={upload} className="flex flex-col gap-3">
        <label htmlFor="timetable-file" className="text-xs font-bold uppercase tracking-wide text-ink-muted">
          Timetable file (.csv / .xlsx)
        </label>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <input
            ref={inputRef}
            id="timetable-file"
            type="file"
            accept=".csv,.xlsx"
            disabled={uploading}
            onChange={(event) => {
              setFile(event.target.files?.[0] || null);
              setError(null);
              setResult(null);
            }}
            className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60"
          />
          <Button type="submit" loading={uploading} disabled={!file}>Upload</Button>
        </div>
        <p className="text-xs leading-relaxed text-ink-muted">
          Required columns: course_code, faculty_employee_id, room, day_of_week, start_time,
          end_time. Maximum file size: 10 MB. Start times below use Asia/Karachi.
        </p>
      </form>

      {error ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Couldn't upload the timetable">{error.message}</StatusBanner>
        </div>
      ) : null}

      {result ? (
        <div className="mt-4 flex flex-col gap-4">
          <StatusBanner
            tone={result.rows_rejected.length ? "warning" : result.rows_created ? "success" : "info"}
            title="Timetable upload complete"
          >
            {result.rows_created} rows created; {result.rows_rejected.length} rows rejected.
          </StatusBanner>
          <section>
            <h3 className="text-sm font-bold text-ink">Rows created</h3>
            {result.classes.length ? (
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-line text-xs font-bold uppercase text-ink-muted">
                    <tr>
                      <th className="px-3 py-2">Course</th>
                      <th className="px-3 py-2">Faculty</th>
                      <th className="px-3 py-2">Room</th>
                      <th className="px-3 py-2">Start · Asia/Karachi</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {result.classes.map((entry) => (
                      <tr key={entry.id}>
                        <td className="px-3 py-2 font-semibold">{entry.courseCode}</td>
                        <td className="px-3 py-2">{professorLabel(entry.professorId)}</td>
                        <td className="px-3 py-2">{entry.room}</td>
                        <td className="px-3 py-2">{formatDateTime(entry.startTime)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="mt-2 text-sm text-ink-muted">No rows created.</p>}
          </section>
          <section>
            <h3 className="text-sm font-bold text-ink">Rows rejected</h3>
            {result.rows_rejected.length ? (
              <ul className="mt-2 flex flex-col gap-2">
                {result.rows_rejected.map((entry) => (
                  <li key={entry.row} className="rounded-lg border border-line bg-warning-soft px-3 py-2 text-sm">
                    <p className="font-bold text-ink">Spreadsheet row {entry.row}</p>
                    <ul className="mt-1 list-disc pl-5 text-ink-muted">
                      {entry.reasons.map((reason, index) => <li key={index}>{reason}</li>)}
                    </ul>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-2 text-sm text-ink-muted">No rows rejected.</p>}
          </section>
        </div>
      ) : null}
    </Card>
  );
}

/**
 * HOD dashboard (Module 2 audit trail): co-sign pending record-change
 * proposals — the second of the two signatures that executes them on-chain —
 * and review the executed audit trail. Executions witnessed here are lifted
 * into page-level state so the Record History tab survives tab switches.
 */
export default function HodPage() {
  const [tab, setTab] = useState("pending");
  const [executedEntries, setExecutedEntries] = useState([]);

  function handleExecuted(entry) {
    setExecutedEntries((prev) => [entry, ...prev]);
  }

  return (
    <PageLayout
      role="HOD"
      title="HOD Dashboard"
      description="Co-sign academic record changes so no single actor can alter them alone."
    >
      <TimetableUploadCard />
      <InstitutionExportCard />
      <div role="tablist" aria-label="Audit trail sections" className="flex flex-wrap gap-2">
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

      {tab === "pending" ? (
        <PendingApprovalsCard onExecuted={handleExecuted} />
      ) : (
        <RecordHistoryCard entries={executedEntries} />
      )}
    </PageLayout>
  );
}
