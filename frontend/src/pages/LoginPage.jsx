import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";
import { getRolePath, getToken, loadUser, login } from "../lib/auth.js";

const INPUT_CLASS = "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const LINK_CLASS = "font-semibold text-primary hover:text-primary-strong";

const REGISTRATION_LINKS = [
  { to: "/register/faculty", label: "Faculty registration" },
  { to: "/register/hod", label: "HOD registration" },
  { to: "/register/student", label: "Student registration (dev)" },
];

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(() => Boolean(getToken()));
  const [error, setError] = useState(null);
  const [setupNeeded, setSetupNeeded] = useState(false);
  const busy = submitting || checking;

  // Purely additive hint: if the status call fails (chain down, backend
  // older than this page) the login form must still work, so nothing shows.
  useEffect(() => {
    let active = true;
    api("/governance/status")
      .then((status) => { if (active) setSetupNeeded(status?.locked === false); })
      .catch(() => { /* keep the login form usable */ });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    let active = true;
    loadUser()
      .then((user) => {
        if (active) navigate(getRolePath(user.role), { replace: true });
      })
      .catch((failure) => {
        if (active) setError(failure);
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => { active = false; };
  }, [navigate]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (busy) return;
    setSubmitting(true);
    setError(null);
    try {
      const user = await login(email.trim(), password);
      navigate(getRolePath(user.role), { replace: true });
    } catch (failure) {
      setError(failure);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="page page-centered">
      <Card className="w-full max-w-md">
        <h1 className="text-2xl font-bold tracking-tight text-ink">EduLedger</h1>
        <p className="mt-1 text-sm text-ink-muted">Sign in with your university account.</p>
        {setupNeeded ? (
          <div className="mt-4">
            <StatusBanner tone="warning" title="System not initialized">
              The system root isn't locked yet —{" "}
              <Link to="/setup" className={LINK_CLASS}>run the first-run setup</Link>.
            </StatusBanner>
          </div>
        ) : null}
        <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-4" aria-busy={busy}>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="login-email" className={LABEL_CLASS}>Email</label>
            <input
              id="login-email"
              name="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={busy}
              className={INPUT_CLASS}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="login-password" className={LABEL_CLASS}>Password</label>
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              className={INPUT_CLASS}
            />
          </div>
          {error ? (
            <StatusBanner tone="error" title="Couldn't sign in">
              {error.message}
            </StatusBanner>
          ) : null}
          <Button type="submit" loading={busy} className="w-full">
            {checking ? "Checking your account…" : "Sign in"}
          </Button>
        </form>
        <div className="mt-5 border-t border-line pt-4 text-sm text-ink-muted">
          <p>Don't have an account yet?</p>
          <ul className="mt-2 flex flex-col gap-1">
            {REGISTRATION_LINKS.map((link) => (
              <li key={link.to}>
                <Link to={link.to} className={LINK_CLASS}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </div>
      </Card>
    </main>
  );
}
