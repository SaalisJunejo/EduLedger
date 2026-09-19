import { useCallback, useEffect, useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import Spinner from "../ui/Spinner.jsx";
import { api } from "../../lib/api.js";
import { describeRecordsError, ipfsGatewayUrl } from "../../lib/records.js";

// How long an approved card lingers with its "Executed" state before leaving
// the pending list — long enough to read the tx hash during a demo.
const EXECUTED_LINGER_MS = 4000;

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

/**
 * HOD "Pending Approvals" dashboard (Module 2 audit trail).
 *
 * Lists the locally mirrored Pending proposals (GET /records/pending — the
 * mirror exists exactly so this list doesn't scan chain history) and co-signs
 * them via POST /records/<id>/approve with role "hod". The HOD signature is
 * the second of two, so a successful approval executes the change on-chain
 * (RecordUpdated) and the card shows the outcome before disappearing.
 *
 * Approvals run one at a time: the backend signer builds transactions with a
 * fixed account nonce, so two parallel approvals would collide.
 */
export default function PendingApprovalsCard({ onExecuted }) {
  const [pending, setPending] = useState(null); // null = not loaded yet
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [approvingId, setApprovingId] = useState(null); // on-chain id in flight
  const [cardErrors, setCardErrors] = useState({}); // on-chain id -> ApiError
  const [executed, setExecuted] = useState({}); // on-chain id -> approve response

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api("/records/pending");
      setPending(data.pending || []);
    } catch (error) {
      setLoadError(error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function approve(proposal) {
    const id = proposal.onchainProposalId;
    setApprovingId(id);
    setCardErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    try {
      const result = await api(`/records/${id}/approve`, {
        method: "POST",
        body: { role: "hod" },
      });
      setExecuted((prev) => ({ ...prev, [id]: result }));
      if (onExecuted) {
        onExecuted({ proposal, result, executedAtLocal: new Date().toISOString() });
      }
      // Show the on-chain outcome briefly, then drop the card from the list.
      setTimeout(() => {
        setPending((list) =>
          (list || []).filter((item) => item.onchainProposalId !== id)
        );
        setExecuted((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }, EXECUTED_LINGER_MS);
    } catch (error) {
      // E.g. executed by the Exam Controller in the meantime (409) or the
      // chain being down — the banner explains; Refresh reconciles the list.
      setCardErrors((prev) => ({ ...prev, [id]: error }));
    } finally {
      setApprovingId(null);
    }
  }

  const rows = pending || [];
  const countLabel = pending === null ? "Loading…" : `${rows.length} proposal${rows.length === 1 ? "" : "s"} awaiting signature`;

  return (
    <Card
      title="Pending approvals"
      description="Proposals awaiting your co-signature. Approving adds the second signature that executes the change on-chain — with the evidence pinned on IPFS as justification."
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className={LABEL_CLASS}>{countLabel}</p>
        <Button variant="secondary" onClick={load} loading={loading}>
          Refresh
        </Button>
      </div>

      <div className="mt-4">
        {loading && pending === null ? (
          <div className="flex items-center gap-3 rounded-lg bg-surface-muted px-4 py-5 text-ink-muted">
            <Spinner className="h-5 w-5" />
            <p className="text-sm">Loading pending proposals…</p>
          </div>
        ) : loadError ? (
          <StatusBanner tone="error" title="Couldn't load pending proposals" detail={loadError.message}>
            {describeRecordsError(loadError)}
          </StatusBanner>
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line bg-surface-muted px-4 py-10 text-center">
            <p className="text-sm font-semibold text-ink">No pending approvals</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-muted">
              Every proposed record change is co-signed. New proposals appear here as
              instructors submit them.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((proposal) => {
              const id = proposal.onchainProposalId;
              const outcome = executed[id];
              const cardError = cardErrors[id];
              return (
                <li key={id} className="rounded-lg border border-line p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-bold text-ink">
                        Proposal #{id}
                        <span className="ml-2 rounded-full bg-warning-soft px-2.5 py-1 text-xs font-bold text-warning">
                          Pending · 1 of 2 signatures
                        </span>
                      </p>
                      <p className="mt-1 text-sm leading-relaxed text-ink-muted">
                        Student <span className="font-semibold text-ink">#{proposal.studentId}</span> ·{" "}
                        {proposal.field}{" "}
                        <span className="font-semibold text-ink">
                          {proposal.oldValue ? `${proposal.oldValue} → ` : ""}
                          {proposal.newValue}
                        </span>
                      </p>
                      <p className="mt-1 text-xs text-ink-muted">
                        Proposed {formatTime(proposal.createdAt)}
                      </p>
                    </div>
                    <div className="flex flex-col items-end">
                      {outcome ? (
                        <span className="inline-flex items-center rounded-full bg-success-soft px-2.5 py-1 text-xs font-bold text-success">
                          Executed ✅
                        </span>
                      ) : (
                        <Button
                          onClick={() => approve(proposal)}
                          loading={approvingId === id}
                          disabled={approvingId !== null && approvingId !== id}
                        >
                          Approve &amp; Sign
                        </Button>
                      )}
                    </div>
                  </div>

                  <div className="mt-3 rounded-lg bg-surface-muted px-3 py-2.5">
                    <p className={LABEL_CLASS}>Evidence</p>
                    <p className="mt-1 break-all font-mono text-xs text-ink">{proposal.ipfsCid}</p>
                    <a
                      href={ipfsGatewayUrl(proposal.ipfsCid)}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-block text-xs font-semibold text-primary underline hover:text-primary-strong"
                    >
                      View evidence on the IPFS gateway ↗
                    </a>
                  </div>

                  {outcome ? (
                    <div className="mt-3">
                      <StatusBanner
                        tone="success"
                        title={`Executed — ${outcome.signature_count} of 2 signatures collected`}
                        detail={`tx ${outcome.transaction_hash}`}
                      >
                        Record updated on-chain{outcome.executed_at ? ` at ${formatTime(outcome.executed_at)}` : ""} ·
                        block #{outcome.block_number}. This card leaves the list shortly.
                      </StatusBanner>
                    </div>
                  ) : null}

                  {cardError ? (
                    <div className="mt-3">
                      <StatusBanner tone="error" title="Approval failed" detail={cardError.message}>
                        {describeRecordsError(cardError)}
                      </StatusBanner>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
