import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Button from "../components/ui/Button.jsx";
import Card from "../components/ui/Card.jsx";
import StatusBanner from "../components/ui/StatusBanner.jsx";
import { api } from "../lib/api.js";
import { describeAuthError } from "../lib/authMessages.js";

const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-ink focus:outline-2 focus:outline-primary disabled:opacity-60";
const LABEL_CLASS = "text-xs font-bold uppercase tracking-wide text-ink-muted";

// Client-side only; the backend owns every other rule (email domain, ID
// formats, whitelist membership), so nothing institution-specific is pinned
// into this bundle.
const MIN_PASSWORD_LENGTH = 8;

const EMAIL_HELPER = "Use your official university email";

/**
 * The three self-service registration flows of requirements doc sections
 * 1.2 (HOD) and 1.3 (faculty, student). Field order and labels follow the
 * doc; the request bodies follow the backend routes.
 */
const ROLES = {
  hod: {
    path: "/auth/register/hod",
    title: "HOD registration",
    description:
      "Register with the credentials your institution already holds. They are cross-referenced against the institutional whitelist, and a match verifies your account immediately.",
    fields: [
      { name: "employee_id", label: "Employee ID" },
      { name: "department", label: "Department" },
      { name: "full_name", label: "Full Name", autoComplete: "name" },
      { name: "email", label: "Email", type: "email", autoComplete: "email", helper: EMAIL_HELPER },
    ],
    successTitle: "Account created and verified",
  },
  faculty: {
    path: "/auth/register/faculty",
    title: "Faculty registration",
    description:
      "Register with your official employee ID and institutional email. Your password is hashed with Argon2id before any record is written.",
    fields: [
      { name: "employee_id", label: "Employee ID" },
      { name: "full_name", label: "Full Name", autoComplete: "name" },
      { name: "department", label: "Department" },
      { name: "email", label: "Official Email", type: "email", autoComplete: "email", helper: EMAIL_HELPER },
    ],
    successTitle: "Account created",
  },
  student: {
    path: "/auth/register/student",
    title: "Student registration (development client)",
    description:
      "Students normally register in the EduLedger mobile app — this page is the development client for demos and testing on the web.",
    fields: [
      { name: "full_name", label: "Full Name", autoComplete: "name" },
      { name: "roll_number", label: "Roll Number" },
      { name: "batch", label: "Batch" },
      { name: "department", label: "Department" },
      {
        name: "email",
        label: "Official University Email",
        type: "email",
        autoComplete: "email",
        helper: EMAIL_HELPER,
      },
    ],
    successTitle: "Account created",
  },
};

export default function RegisterPage({ role }) {
  const config = ROLES[role];
  const navigate = useNavigate();
  const [values, setValues] = useState({});
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);
  const [apiFailure, setApiFailure] = useState(null);
  const [created, setCreated] = useState(null);

  function setField(name, value) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);
    setApiFailure(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setFormError(`Your password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setFormError("The two passwords don't match.");
      return;
    }

    const body = { password };
    for (const field of config.fields) {
      body[field.name] = (values[field.name] || "").trim();
    }

    setSubmitting(true);
    try {
      const data = await api(config.path, { method: "POST", body });
      setCreated(data);
    } catch (failure) {
      setApiFailure(failure);
    } finally {
      setSubmitting(false);
    }
  }

  if (created) {
    return (
      <main className="page page-centered">
        <Card className="w-full max-w-md">
          <StatusBanner tone="success" title={config.successTitle} detail={created.message}>
            Sign in with your institutional email and the password you just chose.
          </StatusBanner>
          <Button className="mt-4 w-full" onClick={() => navigate("/login")}>
            Go to login
          </Button>
        </Card>
      </main>
    );
  }

  return (
    <main className="page page-centered">
      <Card className="w-full max-w-md" title={config.title} description={config.description}>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" aria-busy={submitting}>
          {config.fields.map((field) => (
            <div key={field.name} className="flex flex-col gap-1.5">
              <label htmlFor={`register-${field.name}`} className={LABEL_CLASS}>
                {field.label}
              </label>
              <input
                id={`register-${field.name}`}
                name={field.name}
                type={field.type || "text"}
                autoComplete={field.autoComplete || "off"}
                required
                value={values[field.name] || ""}
                onChange={(event) => setField(field.name, event.target.value)}
                disabled={submitting}
                className={INPUT_CLASS}
              />
              {field.helper ? <p className="text-xs text-ink-muted">{field.helper}</p> : null}
            </div>
          ))}
          <div className="flex flex-col gap-1.5">
            <label htmlFor="register-password" className={LABEL_CLASS}>
              Password
            </label>
            <input
              id="register-password"
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
            <label htmlFor="register-confirm" className={LABEL_CLASS}>
              Confirm password
            </label>
            <input
              id="register-confirm"
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
          {formError ? (
            <StatusBanner tone="error" title="Check the form">
              {formError}
            </StatusBanner>
          ) : null}
          {apiFailure ? (
            <StatusBanner tone="error" title="Couldn't create the account" detail={apiFailure.message}>
              {describeAuthError(apiFailure)}
            </StatusBanner>
          ) : null}
          <Button type="submit" loading={submitting} className="w-full">
            Create account
          </Button>
        </form>
        <p className="mt-4 text-sm text-ink-muted">
          Already registered?{" "}
          <Link to="/login" className="font-semibold text-primary hover:text-primary-strong">
            Sign in
          </Link>
        </p>
      </Card>
    </main>
  );
}
