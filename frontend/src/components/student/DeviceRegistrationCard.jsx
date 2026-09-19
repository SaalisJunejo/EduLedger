import { useState } from "react";
import Button from "../ui/Button.jsx";
import Card from "../ui/Card.jsx";
import StatusBanner from "../ui/StatusBanner.jsx";
import { describeApiError } from "../../lib/attendanceMessages.js";

/**
 * One-time device binding. register-device needs a student_id and the app has
 * no student login yet, so the ID is entered here once and stored — later
 * visits skip this card silently.
 */
export default function DeviceRegistrationCard({
  busy,
  error,
  knownStudentId,
  onRegister,
  onUseRegisteredDevice,
}) {
  const [studentId, setStudentId] = useState(knownStudentId || "");

  const parsed = Number(studentId);
  const valid = Number.isInteger(parsed) && parsed > 0;

  return (
    <Card
      title="Register this device"
      description="One-time step: this browser gets bound to your student record, so attendance can only be marked from your device."
    >
      <div className="flex flex-col gap-3 sm:flex-row">
        <label className="sr-only" htmlFor="student-id-input">
          Student ID
        </label>
        <input
          id="student-id-input"
          type="number"
          min="1"
          inputMode="numeric"
          placeholder="Student ID (e.g. 1)"
          value={studentId}
          onChange={(event) => setStudentId(event.target.value)}
          disabled={busy}
          className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60"
        />
        <Button onClick={() => onRegister(parsed)} loading={busy} disabled={!valid} className="sm:w-44">
          Register Device
        </Button>
      </div>

      {error ? (
        <div className="mt-4 flex flex-col gap-2">
          <StatusBanner tone="error" title="Device registration failed" detail={error.message}>
            {describeApiError(error)}
          </StatusBanner>
          {onUseRegisteredDevice ? (
            <Button variant="secondary" onClick={onUseRegisteredDevice} className="self-start">
              Continue with the registered device (demo)
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
