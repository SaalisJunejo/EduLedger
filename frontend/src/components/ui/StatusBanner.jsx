const TONES = {
  success: "border-success/30 bg-success-soft text-success",
  error: "border-danger/30 bg-danger-soft text-danger-strong",
  warning: "border-warning/40 bg-warning-soft text-warning",
  info: "border-primary/30 bg-primary-soft text-primary-strong",
};

/**
 * Success/error/warning/info banner of the design foundation. `detail` is an
 * optional small line for the raw server message, so failures stay
 * debuggable during demos without drowning the human-readable text.
 */
export default function StatusBanner({ tone = "info", title, detail, className = "", children }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`rounded-lg border px-4 py-3 text-sm ${TONES[tone] ?? TONES.info} ${className}`}
    >
      {title ? <p className="font-semibold">{title}</p> : null}
      {children ? <div className={`leading-relaxed ${title ? "mt-0.5" : ""}`}>{children}</div> : null}
      {detail ? <p className="mt-1 break-words text-xs opacity-75">{detail}</p> : null}
    </div>
  );
}
