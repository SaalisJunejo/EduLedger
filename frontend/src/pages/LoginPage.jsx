import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { getRolePath, getToken, loadUser, login } from "../lib/auth.js";

const INPUT_CLASS = "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(() => Boolean(getToken()));
  const [error, setError] = useState(null);
  const busy = submitting || checking;

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
      </Card>
    </main>
  );
}
