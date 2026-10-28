import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import Spinner from "../components/ui/Spinner.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";
import { describeAuthError } from "../lib/authMessages.js";

const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

const MIN_PASSWORD_LENGTH = 8;

/**
 * First-run setup (requirements doc section 1.1): the System Admin locks the
 * root address on-chain and gets the first ADMIN account. The on-chain lock is
 * permanent and is what makes this a one-time screen — the page only reads
 * GET /governance/status to decide what to show.
 */
export default function SetupPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [secret, setSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);
  const [apiFailure, setApiFailure] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    setStatus(null);
    api("/governance/status")
      .then((data) => { if (active) setStatus(data); })
      .catch((failure) => { if (active) setLoadError(failure); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);
    setApiFailure(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setFormError(`The admin password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setFormError("The two passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      const data = await api("/auth/bootstrap/admin", {
        method: "POST",
        body: {
          full_name: fullName.trim(),
          email: email.trim(),
          password,
          bootstrap_secret: secret,
        },
      });
      setSecret("");
      setResult(data);
    } catch (failure) {
      setApiFailure(failure);
    } finally {
      setSubmitting(false);
    }
  }

  function body() {
    if (loading) {
      return (
        <div className="flex items-center gap-2 text-sm text-ink-muted" role="status">
          <Spinner /> Checking the system root…
        </div>
      );
    }

    if (loadError) {
      return (
        <>
          <StatusBanner tone="error" title="Couldn't read the system status" detail={loadError.message}>
            {describeAuthError(loadError)}
          </StatusBanner>
          <Button className="mt-4" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </Button>
        </>
      );
    }

    if (result) {
      return (
        <>
          <StatusBanner tone="success" title="System root locked" detail={result.message}>
            The root administrative address is now locked on-chain — governance permissions are
            established and this screen can't run again.
          </StatusBanner>
          <dl className="mt-4 text-sm">
            <dt className={LABEL_CLASS}>Transaction hash</dt>
            <dd className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
              {result.transactionHash}
            </dd>
          </dl>
          {result.created === false ? (
            <div className="mt-4">
              <StatusBanner tone="warning" title="Existing admin account kept">
                An admin account with this email already existed; its password was not changed.
              </StatusBanner>
            </div>
          ) : null}
          <Button className="mt-4 w-full" onClick={() => navigate("/login")}>
            Go to login
          </Button>
        </>
      );
    }

    if (status?.locked) {
      return (
        <>
          <StatusBanner tone="info" title="System already initialized">
            The system root is locked on-chain to <span className="font-mono">{status.rootAdmin}</span>.
            Nothing to set up here.
          </StatusBanner>
          <p className="mt-4 text-sm text-ink-muted">
            <Link to="/login" className="font-semibold text-primary hover:text-primary-strong">
              Go to login
            </Link>
          </p>
        </>
      );
    }

    return (
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" aria-busy={submitting}>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-full-name" className={LABEL_CLASS}>Full Name</label>
          <input
            id="setup-full-name"
            name="full_name"
            type="text"
            autoComplete="name"
            required
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            disabled={submitting}
            className={INPUT_CLASS}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-email" className={LABEL_CLASS}>Email</label>
          <input
            id="setup-email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={submitting}
            className={INPUT_CLASS}
          />
          <p className="text-xs text-ink-muted">Use your official university email</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-password" className={LABEL_CLASS}>Password</label>
          <input
            id="setup-password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={submitting}
            className={INPUT_CLASS}
          />
          <p className="text-xs text-ink-muted">At least {MIN_PASSWORD_LENGTH} characters</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-confirm" className={LABEL_CLASS}>Confirm password</label>
          <input
            id="setup-confirm"
            name="confirm_password"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            disabled={submitting}
            className={INPUT_CLASS}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-secret" className={LABEL_CLASS}>Bootstrap secret</label>
          <input
            id="setup-secret"
            name="bootstrap_secret"
            type="password"
            autoComplete="off"
            required
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            disabled={submitting}
            className={INPUT_CLASS}
          />
          <p className="text-xs text-ink-muted">The one-time secret configured on the server</p>
        </div>
        {formError ? (
          <StatusBanner tone="error" title="Check the form">{formError}</StatusBanner>
        ) : null}
        {apiFailure ? (
          <StatusBanner tone="error" title="Couldn't initialize the system" detail={apiFailure.message}>
            {describeAuthError(apiFailure)}
          </StatusBanner>
        ) : null}
        <Button type="submit" loading={submitting} className="w-full">
          Lock the system root
        </Button>
      </form>
    );
  }

  return (
    <main className="page page-centered">
      <Card
        className="w-full max-w-md"
        title="System initialization"
        description="One-time setup: lock the root administrative address on-chain and create the System Admin account."
      >
        {body()}
      </Card>
    </main>
  );
}
