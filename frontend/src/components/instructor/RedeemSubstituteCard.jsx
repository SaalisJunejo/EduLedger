import { useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { api } from "../../lib/api.js";
import { describeWorkloadError, professorLabel } from "../../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60";

/**
 * "Redeem a substitute key" — the other half of the substitute flow.
 *
 * This represents a colleague logging in as themselves on their own
 * dashboard: they paste the key a professor issued to their authenticated
 * account. Redeeming starts the class's live session with the substitute as
 * conductor, so the eventual workload entry credits them — the parent tab
 * renders the QR projector view with a "covering as substitute" banner.
 */
export default function RedeemSubstituteCard({ user, onRedeemed }) {
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null); // ApiError or { message } for validation

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;

    if (!token.trim()) {
      setError({ message: "Paste the substitute key first — the full token the professor issued." });
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const data = await api("/faculty/substitute/redeem", {
        method: "POST",
        body: { token: token.trim() },
      });
      setToken("");
      onRedeemed(data);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      title="Redeem a substitute key"
      description="Covering a colleague's class? Paste the key they issued you. Redeeming starts the live session immediately — the workload credits you as the substitute, not the scheduled professor."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className={LABEL_CLASS}>Redeeming as</p>
            <p className="mt-1.5 rounded-lg border border-line bg-surface-muted px-3 py-2.5 text-sm text-ink">
              {user.fullName || user.email} · {professorLabel(user.id)}
            </p>
            <p className="mt-1 text-xs text-ink-muted">
              Only a key issued for your logged-in account can be redeemed.
            </p>
          </div>
          <div>
            <label htmlFor="redeem-token" className={LABEL_CLASS}>
              Substitute key
            </label>
            <input
              id="redeem-token"
              type="text"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              disabled={submitting}
              placeholder="Paste the full key (eyJhbGciOiJIUzI1NiIs…)"
              className={`${INPUT_CLASS} font-mono`}
            />
            <p className="mt-1 text-xs text-ink-muted">
              Keys expire 30 minutes after issue and can be redeemed once.
            </p>
          </div>
        </div>

        {error ? (
          <StatusBanner
            tone="error"
            title="Couldn't redeem the key"
            detail={error.status ? error.message : undefined}
          >
            {describeWorkloadError(error)}
          </StatusBanner>
        ) : null}

        <div>
          <Button type="submit" loading={submitting}>
            Redeem Key
          </Button>
        </div>
      </form>
    </Card>
  );
}
