import PageLayout from "../components/PageLayout.jsx";

const FEATURES = [
  "Start a class session and project the rotating QR code (5-second nonce)",
  "Create record-change proposals with IPFS-pinned justification evidence",
  "Issue short-lived substitute keys and declare zero-attendance sessions",
  "Track workload credit: conducted, unconducted, and substitute classes",
];

export default function InstructorPage() {
  return (
    <PageLayout
      role="Instructor"
      title="Instructor Dashboard"
      description="Run sessions, propose record changes, and keep workload credit accurate."
      features={FEATURES}
    />
  );
}
