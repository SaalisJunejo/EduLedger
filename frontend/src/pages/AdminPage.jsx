import PageLayout from "../components/PageLayout.jsx";

const FEATURES = [
  "Manage timetables, rooms, and department accounts",
  "Approve student device re-binding requests",
  "Monitor system health: Hardhat node, IPFS daemon, background jobs",
  "Review on-chain events across attendance, record changes, and workload",
];

export default function AdminPage() {
  return (
    <PageLayout
      role="Admin"
      title="Admin Dashboard"
      description="Keep the platform running: schedules, accounts, and system-wide status."
      features={FEATURES}
    />
  );
}
