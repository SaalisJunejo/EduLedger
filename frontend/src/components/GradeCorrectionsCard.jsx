import { useEffect, useRef, useState } from "react";
import Button from "./ui/Button.jsx";
import Card from "./ui/Card.jsx";
import Spinner from "./ui/Spinner.jsx";
import StatusBanner from "./ui/StatusBanner.jsx";
import { api } from "../lib/api.js";
import { describeGradesError } from "../lib/gradesMessages.js";
import { ipfsGatewayUrl } from "../lib/records.js";
import { formatDateTime } from "../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const STATUS_STYLES = {
  Pending: "bg-warning-soft text-warning",
  Approved: "bg-success-soft text-success",
  Rejected: "bg-danger-soft text-danger-strong",
};

function StatusChip({ status }) {
  // The unchanged backend/contract calls an approved correction Executed.
  const label = status === "Executed" ? "Approved" : status;
  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${STATUS_STYLES[label] || "bg-surface-muted text-ink-muted"}`}>
      {label}
    </span>
  );
}

/** Shared role-scoped list. Review controls are enabled only by the HOD page. */
export default function GradeCorrectionsCard({
  title = "Grade corrections",
  canReview = false,
  refreshKey = 0,
  onRefresh,
}) {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [version, setVersion] = useState(0);
  const [action, setAction] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [rejectId, setRejectId] = useState(null);
  const [reason, setReason] = useState("");
  const busy = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    api("/grades/corrections")
      .then((data) => { if (active) setRows(data.corrections); })
      .catch((error) => { if (active) setLoadError(error); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [version, refreshKey]);

  function refresh() {
    if (busy.current) return;
    if (onRefresh) onRefresh();
    else setVersion((value) => value + 1);
  }

  async function decide(correction, kind) {
    if (!canReview || busy.current || loading || correction.status !== "Pending") return;
    const id = correction.onchainProposalId;
    const rejectionReason = reason.trim();
    if (kind === "reject" && !rejectionReason) {
      setActionError({ id, error: { message: "Enter a reason for rejecting this grade correction." } });
      return;
    }
    // Serialize decisions: approvals share the backend's HOD chain signer.
    busy.current = true;
    setAction({ id, kind });
    setActionError(null);
    setNotice(null);
    try {
      const result = await api(`/grades/corrections/${id}/${kind}`, {
        method: "POST",
        ...(kind === "reject" ? { body: { reason: rejectionReason } } : {}),
      });
      if (!mounted.current) return;
      setRows((current) => (current || []).map((row) =>
        row.onchainProposalId === id ? result.correction : row
      ));
      setNotice({ id, kind, transactionHash: result.transaction_hash });
      setRejectId(null);
      setReason("");
    } catch (error) {
      if (mounted.current) setActionError({ id, error });
    } finally {
      busy.current = false;
      if (mounted.current) {
        setAction(null);
        // Reconcile every action, including conflicts from another HOD.
        setVersion((value) => value + 1);
      }
    }
  }

  const sorted = [...(rows || [])].sort((a, b) =>
    Number(b.status === "Pending") - Number(a.status === "Pending")
  );

  return (
    <Card
      title={title}
      description={canReview
        ? "Review grade corrections, pending first. Approving updates the grade on-chain and locally. Rejecting releases its lock without changing the grade."
        : "Grade correction requests and their latest status. Pending corrections keep the current grade locked until the HOD decides."}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className={LABEL_CLASS}>
          {rows === null ? "Loading…" : `${rows.length} grade correction${rows.length === 1 ? "" : "s"}`}
        </p>
        <Button variant="secondary" onClick={refresh} loading={loading} disabled={Boolean(action)}>
          Refresh
        </Button>
      </div>

      {notice ? (
        <StatusBanner className="mt-4" tone="success" title={`Grade correction #${notice.id} ${notice.kind === "approve" ? "approved" : "rejected"}`}>
          {notice.kind === "approve"
            ? "The new grade is in effect and its lock has been cleared."
            : "The current grade is unchanged and its lock has been cleared. Rejection is local only; the on-chain record remains Pending."}
          {notice.transactionHash ? <p className="mt-2 break-all font-mono text-xs">Transaction: {notice.transactionHash}</p> : null}
        </StatusBanner>
      ) : null}

      {loadError ? (
        <StatusBanner className="mt-4" tone="error" title="Couldn't refresh grade corrections">
          {describeGradesError(loadError)}
          {rows !== null ? " Showing the last loaded list; refresh before reviewing." : null}
        </StatusBanner>
      ) : null}

      {loading && rows === null ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-ink-muted" role="status">
          <Spinner /> Loading grade corrections…
        </div>
      ) : rows !== null && sorted.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-line bg-surface-muted p-5 text-sm text-ink-muted">
          No grade corrections yet.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-4">
          {sorted.map((correction) => {
            const id = correction.onchainProposalId;
            const pending = correction.status === "Pending";
            const rejecting = rejectId === id;
            return (
              <li key={id} className="rounded-lg border border-line p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-sm font-bold text-ink">Grade correction #{id}</h3>
                  <div className="flex items-center gap-2">
                    <StatusChip status={correction.status} />
                    {pending ? <span className="text-xs font-semibold text-warning">Locked</span> : null}
                  </div>
                </div>
                <p className="mt-2 break-words text-sm text-ink">
                  Student #{correction.studentId} · {correction.courseCode} ·{" "}
                  <span className="font-semibold">{correction.oldGrade} → {correction.newGrade}</span>
                </p>
                <p className="mt-2 text-xs text-ink-muted">
                  Proposer: {correction.proposerName || `Faculty #${correction.proposerId}`} ·{" "}
                  Requested {formatDateTime(correction.createdAt)} · Asia/Karachi
                </p>
                <p className={`${LABEL_CLASS} mt-3`}>Justification</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink">{correction.justification}</p>
                {correction.ipfsCid ? (
                  <a href={ipfsGatewayUrl(correction.ipfsCid)} target="_blank" rel="noreferrer"
                    className="mt-3 inline-block break-all text-sm font-semibold text-primary underline hover:text-primary-strong">
                    View IPFS evidence ↗
                  </a>
                ) : null}
                {correction.rejectionReason ? (
                  <div className="mt-3 rounded-lg bg-danger-soft p-3 text-sm text-danger-strong">
                    <p className="font-semibold">Rejection reason</p>
                    <p className="mt-1 whitespace-pre-wrap break-words">{correction.rejectionReason}</p>
                  </div>
                ) : null}

                {canReview && pending ? (
                  <div className="mt-4">
                    <div className="flex flex-wrap gap-2">
                      <Button onClick={() => decide(correction, "approve")}
                        loading={action?.id === id && action.kind === "approve"}
                        disabled={Boolean(action) || loading || Boolean(loadError) || rejecting}>
                        Approve
                      </Button>
                      <Button variant="danger" disabled={Boolean(action) || loading || Boolean(loadError)}
                        onClick={() => { setRejectId(id); setReason(""); setActionError(null); }}>
                        Reject
                      </Button>
                    </div>
                    {rejecting ? (
                      <form className="mt-3 flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); decide(correction, "reject"); }}>
                        <label htmlFor={`grade-reject-${id}`} className={LABEL_CLASS}>Reason for rejection</label>
                        <textarea id={`grade-reject-${id}`} value={reason} required autoFocus
                          onChange={(event) => setReason(event.target.value)} disabled={Boolean(action)} rows={3}
                          className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60" />
                        <div className="flex flex-wrap gap-2">
                          <Button type="submit" variant="danger" loading={action?.id === id && action.kind === "reject"}
                            disabled={Boolean(action) || loading || Boolean(loadError) || !reason.trim()}>
                            Confirm rejection
                          </Button>
                          <Button variant="secondary" disabled={Boolean(action)} onClick={() => { setRejectId(null); setReason(""); setActionError(null); }}>
                            Cancel
                          </Button>
                        </div>
                      </form>
                    ) : null}
                  </div>
                ) : null}
                {actionError?.id === id ? (
                  <StatusBanner className="mt-3" tone="error" title="Couldn't complete the grade correction action">
                    {describeGradesError(actionError.error)}
                  </StatusBanner>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
