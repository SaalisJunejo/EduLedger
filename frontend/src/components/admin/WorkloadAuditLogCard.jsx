import { useCallback, useEffect, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import { api } from "../../lib/api.js";
import {
  describeWorkloadError,
  formatDateTime,
  professorLabel,
  shortHash,
  statusMeta,
} from "../../lib/workload.js";

const TH_CLASS =
  "px-3 py-2 text-left text-xs font-bold uppercase tracking-wide text-ink-muted first:pl-0 last:pr-0";
const TD_CLASS = "px-3 py-3 align-top first:pl-0 last:pr-0";

/**
 * Admin "Workload Audit Log" (Module 3): every SessionLog entry, newest
 * first, straight from GET /faculty/logs — student scans, zero-attendance
 * declarations, substitute coverage and the background auto-flag, each tied
 * to the on-chain transaction that anchored it. Tx hashes render short and
 * mono; hovering shows the full hash and clicking copies it.
 */
export default function WorkloadAuditLogCard() {
  const [logs, setLogs] = useState(null); // null = not loaded yet
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [copiedHash, setCopiedHash] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api("/faculty/logs");
      setLogs(data.logs || []);
    } catch (error) {
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function copyHash(hash) {
    try {
      await navigator.clipboard.writeText(hash);
      setCopiedHash(hash);
      setTimeout(() => setCopiedHash(null), 2000);
    } catch {
      /* clipboard unavailable — the title attribute still shows the full hash */
    }
  }

  const rows = logs || [];
  const countLabel =
    logs === null
      ? "Loading…"
      : `${rows.length} workload ${rows.length === 1 ? "entry" : "entries"} · newest first`;

  return (
    <Card
      title="Workload audit log"
      description="Every class outcome anchored on the faculty workload ledger, newest first. A professor marked “substitute” covered the class for its scheduled professor — the workload credits whoever actually taught."
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs font-bold uppercase tracking-wide text-ink-muted">{countLabel}</p>
        <Button variant="secondary" onClick={load} loading={loading}>
          Refresh
        </Button>
      </div>

      <div className="mt-4">
        {loading && logs === null ? (
          <div className="flex items-center gap-3 rounded-lg bg-surface-muted px-4 py-5 text-ink-muted">
            <Spinner className="h-5 w-5" />
            <p className="text-sm">Loading the workload audit log…</p>
          </div>
        ) : loadError && logs === null ? (
          <StatusBanner
            tone="error"
            title="Couldn't load the workload audit log"
            detail={loadError.status ? loadError.message : undefined}
          >
            {describeWorkloadError(loadError)}
          </StatusBanner>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line bg-surface-muted px-4 py-10 text-center">
            <p className="text-sm font-semibold text-ink">No workload entries yet</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">
              Class outcomes anchor here the moment they land on-chain — a student scan, a
              zero-attendance declaration, a substitute concluding a covered class, or the
              background job flagging a class as un-conducted.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-line">
                  <th className={TH_CLASS}>Class</th>
                  <th className={TH_CLASS}>Professor</th>
                  <th className={TH_CLASS}>Status</th>
                  <th className={TH_CLASS}>Timestamp</th>
                  <th className={TH_CLASS}>On-chain tx</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((log) => {
                  const meta = statusMeta(log.status);
                  const scheduledProfessorId = log.class ? log.class.professorId : null;
                  const substituteCovered =
                    Boolean(scheduledProfessorId) && log.conductedBy !== scheduledProfessorId;
                  return (
                    <tr key={log.id} className="border-b border-line last:border-0">
                      <td className={TD_CLASS}>
                        <p className="font-semibold text-ink">
                          {log.class ? log.class.courseId : `Class #${log.classId}`}
                        </p>
                        <p className="mt-0.5 text-xs text-ink-muted">
                          {log.class ? `${log.class.room} · #${log.classId}` : "scheduled class deleted"}
                        </p>
                      </td>
                      <td className={TD_CLASS}>
                        <p className="font-semibold text-ink">{professorLabel(log.conductedBy)}</p>
                        {substituteCovered ? (
                          <span className="mt-1 inline-flex items-center rounded-full bg-warning-soft px-2.5 py-1 text-xs font-bold text-warning">
                            substitute
                          </span>
                        ) : null}
                      </td>
                      <td className={TD_CLASS}>
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold ${meta.chip}`}
                        >
                          {meta.label}
                        </span>
                      </td>
                      <td className={`${TD_CLASS} whitespace-nowrap text-ink-muted`}>
                        {formatDateTime(log.timestamp)}
                      </td>
                      <td className={TD_CLASS}>
                        <button
                          type="button"
                          title={log.onchainTxHash}
                          onClick={() => copyHash(log.onchainTxHash)}
                          className="font-mono text-xs text-primary underline decoration-dotted underline-offset-2 hover:text-primary-strong"
                        >
                          {copiedHash === log.onchainTxHash
                            ? "Copied ✓"
                            : shortHash(log.onchainTxHash)}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {loadError && logs !== null ? (
        <div className="mt-4">
          <StatusBanner
            tone="warning"
            title="Refresh failed"
            detail={loadError.status ? loadError.message : undefined}
          >
            {describeWorkloadError(loadError)} — the table above shows the last successfully
            loaded entries.
          </StatusBanner>
        </div>
      ) : null}
    </Card>
  );
}
