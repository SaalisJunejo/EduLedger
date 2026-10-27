import { Link, useNavigate } from "react-router-dom";
import Button from "./ui/Button.jsx";
import { logout } from "../lib/auth.js";

/**
 * Shared shell for role dashboards. Each dashboard grows feature widgets
 * (QR scan, proposals, workload tables) as the MVP modules are implemented.
 *
 * Pages that pass `children` render their real module UI in the content area;
 * pages without children keep the scaffold placeholder card (HOD/Admin until
 * Phase 3/4). The header/nav/role-badge structure is identical in both modes.
 */
export default function PageLayout({ role, title, description, features = [], children }) {
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <main className="page">
      <header className="page-header">
        <Link to="/login" className="brand">
          EduLedger
        </Link>
        <div className="flex items-center gap-3">
          <span className="role-badge">{role}</span>
          <Button variant="secondary" onClick={handleLogout}>Logout</Button>
        </div>
      </header>

      {children ? (
        <>
          <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
          {description ? (
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">{description}</p>
          ) : null}
          <div className="mt-5 flex flex-col gap-5">{children}</div>
        </>
      ) : (
        <section className="card">
          <h1>{title}</h1>
          {description ? <p className="muted">{description}</p> : null}

          {features.length > 0 ? (
            <>
              <h2 className="section-title">Planned in this dashboard</h2>
              <ul className="feature-list">
                {features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
            </>
          ) : null}

          <p className="muted small">
            Scaffold only - feature modules land next (see docs/PRD.md).
          </p>
        </section>
      )}
    </main>
  );
}
