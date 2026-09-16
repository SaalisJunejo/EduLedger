import { Link } from "react-router-dom";

/**
 * Shared shell for role dashboards. Each dashboard grows feature widgets
 * (QR scan, proposals, workload tables) as the MVP modules are implemented.
 */
export default function PageLayout({ role, title, description, features = [] }) {
  return (
    <main className="page">
      <header className="page-header">
        <Link to="/login" className="brand">
          EduLedger
        </Link>
        <span className="role-badge">{role}</span>
      </header>

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
    </main>
  );
}
