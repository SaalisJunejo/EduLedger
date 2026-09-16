import PageLayout from "../components/PageLayout.jsx";

const FEATURES = [
  "Bind this device on first login; re-binding requires admin approval",
  "Identity verification before the scanner unlocks (face, then periocular, then WebAuthn fallback)",
  "Scan the live attendance QR code (server nonce refreshes every 5 seconds)",
  "View locked attendance records with their on-chain transaction hash",
];

export default function StudentPage() {
  return (
    <PageLayout
      role="Student"
      title="Student Dashboard"
      description="Mark attendance in seconds and keep a tamper-proof record of every class."
      features={FEATURES}
    />
  );
}
