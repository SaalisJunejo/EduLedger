import { useEffect, useState } from "react";
import PageLayout from "../components/PageLayout.jsx";
import { InstitutionExportCard } from "../components/ExportReportCard.jsx";
import WorkloadAuditLogCard from "../components/admin/WorkloadAuditLogCard.jsx";
import GradeCorrectionsCard from "../components/GradeCorrectionsCard.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import Spinner from "../components/ui/Spinner.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";

function GovernanceStatusCard() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshVersion, setRefreshVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setStatus(null);
    api("/governance/status")
      .then((data) => { if (active) setStatus(data); })
      .catch((loadError) => { if (active) setError(loadError); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshVersion]);

  return (
    <Card title="Governance status" description="Read-only status of the GovernanceRegistry root lock and the backend admin account.">
      <Button variant="secondary" loading={loading} onClick={() => setRefreshVersion((value) => value + 1)}>
        Refresh
      </Button>
      {loading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-ink-muted" role="status">
          <Spinner /> Loading governance status…
        </div>
      ) : error ? (
        <div className="mt-4">
          <StatusBanner tone="error" title="Couldn't load governance status">{error.message}</StatusBanner>
        </div>
      ) : status ? (
        <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Locked</dt>
            <dd className="mt-1 font-semibold text-ink">{status.locked ? "Yes" : "No"}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Admin user exists</dt>
            <dd className="mt-1 font-semibold text-ink">{status.adminUserExists ? "Yes" : "No"}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Root admin address</dt>
            <dd className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
              {status.rootAdmin}
            </dd>
          </div>
        </dl>
      ) : null}
    </Card>
  );
}

/**
 * Admin dashboard (Module 3): the system-wide workload audit log — every
 * class outcome anchored on the faculty ledger, with its credited conductor
 * and on-chain transaction. Further admin surfaces (timetables, accounts,
 * system health) land with their modules, per docs/PRD.md.
 */
export default function AdminPage() {
  return (
    <PageLayout
      role="Admin"
      title="Admin Dashboard"
      description="Read-only grade correction oversight and the system-wide faculty workload audit log. Grade correction decisions are reserved for the HOD."
    >
      <GovernanceStatusCard />
      <InstitutionExportCard />
      <GradeCorrectionsCard />
      <WorkloadAuditLogCard />
    </PageLayout>
  );
}
