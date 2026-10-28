import { useState } from "react";
import Button from "../ui/Button.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { describeApiError } from "../../lib/attendanceMessages.js";

const CODE_DIGITS = 6;

// Digits only, grouped "123 456" for reading back against the projector.
function groupDigits(digits) {
  return digits.length > 3 ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
}

/**
 * The fallback to the QR scan: type the 6-digit code shown on the
 * projector. Only the code is sent — the backend finds the live session
 * currently showing it (Phase 1.1b).
 */
export default function CodeEntryForm({ busy = false, error = null, onSubmit, onCancel }) {
  const [digits, setDigits] = useState("");
  const complete = digits.length === CODE_DIGITS;

  function handleSubmit(event) {
    event.preventDefault();
    if (complete && !busy) onSubmit(digits);
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-line bg-surface-muted p-3">
      <label htmlFor="attendance-code" className="text-xs font-bold uppercase tracking-wide text-ink-muted">
        6-digit code from the projector
      </label>
      {error ? (
        <div className="mt-2">
          <StatusBanner tone="error" title="Attendance not locked" detail={error.message}>
            {describeApiError(error)}
          </StatusBanner>
        </div>
      ) : null}
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input
          id="attendance-code"
          value={groupDigits(digits)}
          onChange={(event) => setDigits(event.target.value.replace(/\D/g, "").slice(0, CODE_DIGITS))}
          inputMode="numeric"
          pattern="[0-9 ]*"
          autoComplete="one-time-code"
          placeholder="123 456"
          maxLength={CODE_DIGITS + 1}
          disabled={busy}
          autoFocus
          className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-center font-mono text-2xl font-bold tracking-[0.2em] text-ink placeholder:text-ink-muted/50 focus:outline-2 focus:outline-primary disabled:opacity-60"
        />
        <Button type="submit" loading={busy} disabled={!complete} className="sm:w-36">
          Submit
        </Button>
      </div>
      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        className="mt-2 text-xs font-semibold text-primary hover:text-primary-strong disabled:opacity-60"
      >
        Scan the QR instead
      </button>
    </form>
  );
}
