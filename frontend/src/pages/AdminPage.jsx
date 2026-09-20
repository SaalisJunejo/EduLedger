import PageLayout from "../components/PageLayout.jsx";
import WorkloadAuditLogCard from "../components/admin/WorkloadAuditLogCard.jsx";

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
      description="The system-wide record of every class outcome anchored on the faculty workload ledger — student scans, zero-attendance declarations, substitute coverage and automatic un-conducted flags, each tied to its on-chain transaction."
    >
      <WorkloadAuditLogCard />
    </PageLayout>
  );
}
