import { useNavigate } from "react-router-dom";

const DEMO_ROLES = [
  { label: "Student", path: "/student", hint: "Scan attendance, view locked records" },
  { label: "Instructor", path: "/instructor", hint: "Start sessions, propose record changes" },
  { label: "HOD", path: "/hod", hint: "Cosign record changes (2-of-3)" },
  { label: "Admin", path: "/admin", hint: "Timetables, accounts, system status" },
];

export default function LoginPage() {
  const navigate = useNavigate();

  return (
    <main className="page page-centered">
      <div className="card login-card">
        <h1>EduLedger</h1>
        <p className="muted">Decentralized University Trust &amp; Verification Platform</p>

        <h2 className="section-title">Choose a demo role</h2>
        <div className="role-grid">
          {DEMO_ROLES.map((role) => (
            <button
              key={role.path}
              type="button"
              className="role-button"
              onClick={() => navigate(role.path)}
            >
              <strong>{role.label}</strong>
              <span>{role.hint}</span>
            </button>
          ))}
        </div>

        <p className="muted small">
          JWT authentication is not wired yet - role buttons jump straight to each dashboard.
        </p>
      </div>
    </main>
  );
}
