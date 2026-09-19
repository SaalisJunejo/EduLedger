import { useCallback, useState } from "react";
import PageLayout from "../components/PageLayout.jsx";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import Spinner from "../components/ui/Spinner.jsx";
import DeviceRegistrationCard from "../components/student/DeviceRegistrationCard.jsx";
import FaceCaptureCard from "../components/student/FaceCaptureCard.jsx";
import QrScannerCard from "../components/student/QrScannerCard.jsx";
import { api } from "../lib/api.js";
import { describeApiError } from "../lib/attendanceMessages.js";

const DEVICE_KEY = "eduledger.device_id";
const STUDENT_KEY = "eduledger.student_id";

// Step machine of the Mark Attendance flow.
const STEP = { IDLE: "idle", CAMERA: "camera", VERIFYING: "verifying", SCANNER: "scanner", SUCCESS: "success" };

// Identity failures meaning "no usable stored face template" — safe to offer
// the demo enrollment with the just-captured photo.
const ENROLLABLE_CODES = new Set(["face_not_enrolled", "student_not_found", "embedding_provider_mismatch"]);

function readStored() {
  try {
    return {
      deviceId: localStorage.getItem(DEVICE_KEY) || "",
      studentId: localStorage.getItem(STUDENT_KEY) || "",
    };
  } catch {
    return { deviceId: "", studentId: "" };
  }
}

function generateDeviceId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `device-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

export default function StudentPage() {
  const [stored, setStored] = useState(readStored);

  // Device registration (one-time).
  const [regBusy, setRegBusy] = useState(false);
  const [regError, setRegError] = useState(null);
  const [regAttempt, setRegAttempt] = useState(null);
  const [justRegistered, setJustRegistered] = useState(false);

  // Mark Attendance flow.
  const [step, setStep] = useState(STEP.IDLE);
  const [captured, setCaptured] = useState(null); // data URL of the face photo
  const [token, setToken] = useState(null); // {verification_token, expires_in, deadline}
  const [result, setResult] = useState(null); // scan success payload
  const [flowError, setFlowError] = useState(null);
  const [enrollName, setEnrollName] = useState("");
  const [enrollBusy, setEnrollBusy] = useState(false);
  const [submitBusy, setSubmitBusy] = useState(false);

  const registered = Boolean(stored.deviceId && stored.studentId);
  const activeStudentId = stored.studentId || (regAttempt ? String(regAttempt.studentId) : "");

  function persist(deviceId, studentId) {
    try {
      localStorage.setItem(DEVICE_KEY, deviceId);
      localStorage.setItem(STUDENT_KEY, String(studentId));
    } catch {
      /* storage unavailable — keep the values in memory for this visit */
    }
    setStored({ deviceId, studentId: String(studentId) });
  }

  async function handleRegister(studentId) {
    const deviceId = stored.deviceId || generateDeviceId();
    setRegBusy(true);
    setRegError(null);
    setRegAttempt({ studentId, deviceId });
    try {
      await api("/student/register-device", {
        method: "POST",
        body: { student_id: studentId, device_id: deviceId },
      });
      persist(deviceId, studentId);
      setJustRegistered(true);
    } catch (error) {
      setRegError(error);
    } finally {
      setRegBusy(false);
    }
  }

  // Demo shortcut when the backend reports another device is already bound:
  // adopt that device ID on this browser instead of requiring an admin rebind.
  function useRegisteredDevice() {
    const boundDevice = regError && regError.details ? regError.details.current_device_id : null;
    if (!boundDevice || !regAttempt) return;
    persist(boundDevice, regAttempt.studentId);
    setRegError(null);
    setJustRegistered(true);
  }

  async function verifyIdentity(image) {
    setFlowError(null);
    setStep(STEP.VERIFYING);
    try {
      const data = await api("/attendance/verify-identity", {
        method: "POST",
        body: { student_id: Number(activeStudentId), face_image: image },
      });
      // Deadline measured locally so the countdown is immune to clock skew.
      setToken({ ...data, deadline: Date.now() + data.expires_in * 1000 });
      setStep(STEP.SCANNER);
    } catch (error) {
      setFlowError(error);
    }
  }

  const handleScan = useCallback(
    async (payload) => {
      setFlowError(null);
      setSubmitBusy(true);
      try {
        const data = await api("/attendance/scan", {
          method: "POST",
          body: {
            verification_token: token.verification_token,
            device_id: stored.deviceId,
            session_id: payload.session_id,
            nonce: payload.nonce,
          },
        });
        setResult(data);
        setStep(STEP.SUCCESS);
      } catch (error) {
        if (error.code === "attendance_already_locked") {
          // Rejected, but the student IS marked present — show it as a success.
          setResult({
            already_locked: true,
            student_id: Number(activeStudentId),
            session_id: payload.session_id,
          });
          setStep(STEP.SUCCESS);
        } else {
          setFlowError(error); // shown inside the scanner card — rescan after fixing
        }
      } finally {
        setSubmitBusy(false);
      }
    },
    [token, stored.deviceId, activeStudentId]
  );

  async function enrollAndContinue() {
    setEnrollBusy(true);
    setFlowError(null);
    try {
      await api("/student/enroll-face", {
        method: "POST",
        body: {
          student_id: Number(activeStudentId),
          name: enrollName.trim(),
          face_image: captured,
        },
      });
      // A brand-new profile can now accept the device binding that failed earlier.
      if (!registered) {
        try {
          const deviceId = stored.deviceId || generateDeviceId();
          await api("/student/register-device", {
            method: "POST",
            body: { student_id: Number(activeStudentId), device_id: deviceId },
          });
          persist(deviceId, activeStudentId);
          setJustRegistered(true);
        } catch {
          /* if this still fails, the scan's device check will explain why */
        }
      }
      await verifyIdentity(captured);
    } catch (error) {
      setFlowError(error);
    } finally {
      setEnrollBusy(false);
    }
  }

  function resetFlow() {
    setStep(STEP.IDLE);
    setCaptured(null);
    setToken(null);
    setResult(null);
    setFlowError(null);
    setEnrollName("");
    setSubmitBusy(false);
  }

  function spinnerCard(title, text) {
    return (
      <Card title={title}>
        <div className="flex items-center gap-3 rounded-lg bg-surface-muted px-4 py-5 text-ink-muted">
          <Spinner className="h-5 w-5" />
          <p className="text-sm leading-relaxed">{text}</p>
        </div>
      </Card>
    );
  }

  let attendanceBody;
  if (step === STEP.IDLE) {
    attendanceBody = (
      <Card
        title="Mark attendance"
        description="Three steps: capture your face, scan the instructor's live QR code, and your attendance locks on-chain — no proxy signatures, no tampering."
      >
        <Button onClick={() => setStep(STEP.CAMERA)}>Mark Attendance</Button>
      </Card>
    );
  } else if (step === STEP.CAMERA) {
    attendanceBody = (
      <Card
        title="Verify your identity"
        description="Look straight at the camera and capture a photo — it's matched against your enrolled face template."
      >
        <FaceCaptureCard
          onCapture={(image) => {
            setCaptured(image);
            verifyIdentity(image);
          }}
          onCancel={resetFlow}
        />
      </Card>
    );
  } else if (step === STEP.VERIFYING) {
    if (enrollBusy) {
      attendanceBody = spinnerCard(
        "Enrolling your face",
        "Creating your demo enrollment with the captured photo, then verifying it…"
      );
    } else if (flowError) {
      attendanceBody = (
        <Card title="Identity verification failed">
          <div className="flex flex-col gap-4 sm:flex-row">
            {captured ? (
              <img
                src={captured}
                alt="Photo that failed verification"
                className="w-36 self-start rounded-lg border border-line"
              />
            ) : null}
            <div className="flex-1">
              <StatusBanner tone="error" detail={flowError.message}>
                {describeApiError(flowError)}
              </StatusBanner>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  onClick={() => {
                    setFlowError(null);
                    setStep(STEP.CAMERA);
                  }}
                >
                  Try Again
                </Button>
                <Button variant="secondary" onClick={resetFlow}>
                  Cancel
                </Button>
              </div>
              {ENROLLABLE_CODES.has(flowError.code) ? (
                <div className="mt-4 rounded-lg border border-line bg-surface-muted p-3">
                  <p className="text-xs font-bold uppercase tracking-wide text-ink-muted">
                    Demo enrollment
                  </p>
                  <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                    No usable face template is stored for you. Enroll with the photo you
                    just captured (this overwrites any stored template).
                  </p>
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <input
                      value={enrollName}
                      onChange={(event) => setEnrollName(event.target.value)}
                      placeholder="Full name (for your record)"
                      disabled={enrollBusy}
                      className="w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-primary disabled:opacity-60"
                    />
                    <Button
                      onClick={enrollAndContinue}
                      loading={enrollBusy}
                      disabled={!enrollName.trim()}
                      className="sm:w-44"
                    >
                      Enroll &amp; Continue
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </Card>
      );
    } else {
      attendanceBody = spinnerCard(
        "Verifying your identity",
        "Matching this photo against your enrolled face template…"
      );
    }
  } else if (step === STEP.SCANNER) {
    attendanceBody = (
      <Card
        title="Scan the live QR code"
        description="Point your camera at the code projected by your instructor. It rotates every few seconds — scan the one on screen right now."
      >
        <QrScannerCard
          expiresAt={token ? token.deadline : 0}
          windowSeconds={token ? token.expires_in : 30}
          busy={submitBusy}
          error={flowError}
          onScan={handleScan}
          onReverify={() => {
            setToken(null);
            setFlowError(null);
            setStep(STEP.CAMERA);
          }}
          onCancel={() => {
            setToken(null);
            setFlowError(null);
            setStep(STEP.IDLE);
          }}
        />
      </Card>
    );
  } else {
    attendanceBody = (
      <Card>
        <div className="flex items-start gap-3">
          <span className="text-3xl leading-none">✅</span>
          <div>
            <h2 className="text-xl font-bold text-success">Attendance Locked ✅</h2>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">
              {result.already_locked
                ? "Your attendance for this session was already locked on-chain — you're marked present."
                : "Your attendance is recorded on-chain and can't be altered."}
            </p>
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Student</dt>
            <dd className="mt-0.5 font-semibold text-ink">#{result.student_id}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Session</dt>
            <dd className="mt-0.5 font-semibold text-ink">#{result.session_id}</dd>
          </div>
          {result.already_locked ? null : (
            <>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Block</dt>
                <dd className="mt-0.5 font-semibold text-ink">#{result.block_number}</dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-ink-muted">Locked at</dt>
                <dd className="mt-0.5 font-semibold text-ink">{formatTime(result.locked_at)}</dd>
              </div>
            </>
          )}
        </dl>
        {result.transaction_hash ? (
          <div className="mt-4">
            <p className="text-xs font-bold uppercase tracking-wide text-ink-muted">Transaction hash</p>
            <p className="mt-1 break-all rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-ink">
              {result.transaction_hash}
            </p>
          </div>
        ) : null}
        <div className="mt-5">
          <Button variant="secondary" onClick={resetFlow}>
            Done
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <PageLayout
      role="Student"
      title="Student Dashboard"
      description="Verify your identity, scan the live code, and lock your attendance on-chain."
    >
      {registered && !justRegistered ? (
        <p className="text-xs text-ink-muted">
          Student #{stored.studentId} · this device is registered.
        </p>
      ) : null}

      {justRegistered && registered ? (
        <StatusBanner tone="success" title="Device registered">
          This device is now bound to student #{stored.studentId} — future visits skip
          this step.
        </StatusBanner>
      ) : null}

      {!registered ? (
        <DeviceRegistrationCard
          busy={regBusy}
          error={regError}
          knownStudentId={activeStudentId}
          onRegister={handleRegister}
          onUseRegisteredDevice={
            regError && regError.details && regError.details.current_device_id
              ? useRegisteredDevice
              : null
          }
        />
      ) : null}

      {activeStudentId ? attendanceBody : null}
    </PageLayout>
  );
}
