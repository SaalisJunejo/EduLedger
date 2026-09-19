import { useState } from "react";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import PendingApprovalsCard from "../components/hod/PendingApprovalsCard.jsx";
import RecordHistoryCard from "../components/hod/RecordHistoryCard.jsx";

// Dashboard sections as tabs on this route (no new routes — see App.jsx).
const TABS = [
  { id: "pending", label: "Pending Approvals" },
  { id: "history", label: "Record History" },
];

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
