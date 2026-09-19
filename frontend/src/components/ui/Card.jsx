/**
 * Shared content card of the design foundation: rounded corners, a subtle
 * shadow and consistent padding. Phase 3/4 modules reuse it for their panels.
 */
export default function Card({ title, description, footer, className = "", children }) {
  const hasHeader = Boolean(title || description);
  return (
    <section
      className={`rounded-xl border border-line bg-surface p-5 shadow-[0_8px_24px_rgba(15,23,42,0.06)] sm:p-6 ${className}`}
    >
      {title ? <h2 className="text-base font-bold text-ink">{title}</h2> : null}
      {description ? (
        <p className="mt-1 text-sm leading-relaxed text-ink-muted">{description}</p>
      ) : null}
      {children ? <div className={hasHeader ? "mt-4" : ""}>{children}</div> : null}
      {footer ? <div className="mt-4 border-t border-line pt-4">{footer}</div> : null}
    </section>
  );
}
