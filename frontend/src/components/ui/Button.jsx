import Spinner from "./Spinner.jsx";

const VARIANTS = {
  primary: "bg-primary text-white hover:bg-primary-strong",
  secondary: "border border-line bg-surface text-ink hover:border-primary hover:text-primary",
  danger: "bg-danger text-white hover:bg-danger-strong",
};

/**
 * Shared button of the design foundation. `loading` shows a spinner and
 * disables the button; a plain `disabled` only dims it.
 */
export default function Button({
  variant = "primary",
  loading = false,
  disabled = false,
  type = "button",
  className = "",
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-strong disabled:cursor-not-allowed disabled:opacity-60 ${
        VARIANTS[variant] ?? VARIANTS.primary
      } ${className}`}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}
