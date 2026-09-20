import { useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { api } from "../../lib/api.js";
import { PROFESSORS, describeWorkloadError, professorLabel } from "../../lib/workload.js";

const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";
const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60";

/**
 * "Redeem a substitute key" — the other half of the substitute flow.
 *
 * This represents a colleague logging in as themselves on their own
 * dashboard: they paste the key a professor issued them and pick who they
 * are. Redeeming starts the class's live session with the substitute as
 * conductor, so the eventual workload entry credits them — the parent tab
 * renders the QR projector view with a "covering as substitute" banner.
 */
export default function RedeemSubstituteCard({ onRedeemed }) {
  // Demo stand-in for the logged-in professor; default to the substitute
  // the mock roster usually issues keys for.
  const [substituteId, setSubstituteId] = useState(
    PROFESSORS.find((entry) => entry.id !== 101)?.id ?? 101
  );
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
        body: { token: token.trim(), substitute_id: Number(substituteId) },
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
            <label htmlFor="redeem-as" className={LABEL_CLASS}>
              Redeeming as
            </label>
            <select
              id="redeem-as"
              value={substituteId}
              onChange={(event) => setSubstituteId(event.target.value)}
              disabled={submitting}
              className={INPUT_CLASS}
            >
              {PROFESSORS.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {professorLabel(entry.id)}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-ink-muted">
              Demo stand-in for the logged-in account — pick the professor the key was issued for.
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
