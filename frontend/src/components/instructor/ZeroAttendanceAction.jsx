import { useState } from "react";
import Button from "../ui/Button.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { api } from "../../lib/api.js";
import { describeWorkloadError } from "../../lib/workload.js";

/**
 * "Declare Zero Attendance" with its mandatory confirmation step — the
 * declaration anchors CONDUCTED_ZERO_STUDENTS on-chain (one entry per
 * class, irreversible), so it must never fire on a stray click.
 *
 * Shared by the professor's class card and the substitute's live-session
 * view: whoever is the live conductor submits with their own id, so the
 * workload credits the person actually teaching (the substitute when they
 * cover the class).
 */
export default function ZeroAttendanceAction({
  classId,
  conductorId,
  disabled = false,
  disabledHint = "",
  onDeclared,
}) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function declare() {
    setSubmitting(true);
    setError(null);
    try {
      const data = await api("/faculty/session/zero-attendance", {
        method: "POST",
        body: { professor_id: conductorId, class_id: classId },
      });
      setConfirming(false);
      if (onDeclared) onDeclared(data);
    } catch (submitError) {
      setError(submitError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Button
        variant="danger"
        disabled={disabled}
        onClick={() => {
          setConfirming(true);
          setError(null);
        }}
      >
        Declare Zero Attendance
      </Button>
      {disabled && disabledHint ? (
        <p className="max-w-[220px] text-xs leading-relaxed text-ink-muted">{disabledHint}</p>
      ) : null}

      {confirming ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="zero-attendance-title"
            className="w-full max-w-md rounded-xl border border-warning/40 bg-surface p-5 shadow-[0_8px_24px_rgba(15,23,42,0.06)]"
          >
            <h3 id="zero-attendance-title" className="text-base font-bold text-ink">
              Are you sure no students attended?
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-muted">
              This declares the class <span className="font-semibold text-ink">CONDUCTED with zero
              students</span> and anchors it on the workload ledger — one entry per class, and it
              can&apos;t be undone.
            </p>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                variant="secondary"
                onClick={() => setConfirming(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button variant="danger" onClick={declare} loading={submitting}>
                Yes, declare zero attendance
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {error ? (
        <StatusBanner
          tone="error"
          title="Couldn't declare zero attendance"
          detail={error.status ? error.message : undefined}
        >
          {describeWorkloadError(error)}
        </StatusBanner>
      ) : null}
    </div>
  );
}
