import PageLayout from "../components/PageLayout.jsx";

const FEATURES = [
  "Approve or reject record-change proposals (2-of-3 multi-signature)",
  "Cosign alongside the Exam Controller to execute changes on-chain",
  "Inspect the immutable audit trail with IPFS evidence links",
  "Monitor departmental faculty workload distribution",
];

export default function HodPage() {
  return (
    <PageLayout
      role="HOD"
      title="HOD Dashboard"
      description="Co-sign academic record changes so no single actor can alter them alone."
      features={FEATURES}
    />
  );
}
