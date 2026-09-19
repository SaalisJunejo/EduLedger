import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { ipfsGatewayUrl } from "../../lib/records.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

/**
 * Executed record changes — the visible end of the permanent audit trail.
 *
 * Backend gap (surfaced, not faked — same policy as the attendance-count gap):
 * there is no endpoint that lists executed proposals. GET /records/pending
 * only returns Pending rows and GET /records/<id> reads one proposal at a
 * time, so the full trail can't be assembled from today's API. Until a
 * history endpoint lands, this panel shows the executions this dashboard
 * co-signed in the current browser session — real transaction data only.
 */
export default function RecordHistoryCard({ entries }) {
  return (
    <Card
      title="Record history"
      description="Executed record changes with their on-chain transaction hashes and IPFS evidence — the permanent, tamper-proof audit trail."
    >
      <StatusBanner tone="warning" title="Backend gap: no history endpoint yet">
        The API can only list Pending proposals (<code>GET /api/v1/records/pending</code>) and
        fetch one proposal at a time (<code>{"GET /api/v1/records/<id>"}</code>) — no endpoint
        returns executed proposals yet. Until one is added, this panel shows only changes
        co-signed from this dashboard in the current browser session.
      </StatusBanner>

      {entries.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-line bg-surface-muted px-4 py-10 text-center">
          <p className="text-sm font-semibold text-ink">Nothing executed in this session yet</p>
          <p className="mt-1 text-xs leading-relaxed text-ink-muted">
            Approve a pending proposal on the Pending Approvals tab and its on-chain execution
            appears here with its transaction hash and evidence link.
          </p>
        </div>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
          {entries.map(({ proposal, result, executedAtLocal }) => (
            <li key={proposal.onchainProposalId} className="rounded-lg border border-line p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-bold text-ink">
                  Proposal #{proposal.onchainProposalId}
                  <span className="ml-2 rounded-full bg-success-soft px-2.5 py-1 text-xs font-bold text-success">
                    Executed
                  </span>
                </p>
                <p className="text-xs text-ink-muted">
                  executed {formatTime(result.executed_at || executedAtLocal)}
                </p>
              </div>
              <p className="mt-1 text-sm leading-relaxed text-ink-muted">
                Student <span className="font-semibold text-ink">#{proposal.studentId}</span> ·{" "}
                {proposal.field}{" "}
                <span className="font-semibold text-ink">
                  {proposal.oldValue ? `${proposal.oldValue} → ` : ""}
                  {proposal.newValue}
                </span>
              </p>
              <p className="mt-2 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
                {result.transaction_hash}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="text-ink-muted">
                  block #{result.block_number} · {result.signature_count} of 2 signatures
                </span>
                <a
                  href={ipfsGatewayUrl(proposal.ipfsCid)}
                  target="_blank"
                  rel="noreferrer"
                  className="font-semibold text-primary underline hover:text-primary-strong"
                >
                  View evidence on the IPFS gateway ↗
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
