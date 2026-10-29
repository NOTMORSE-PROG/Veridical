import type { ReactNode } from "react";

// A neutral magnifier, not a check mark: an empty list means nothing was
// recorded, which is not the same as the manuscript passing.
function EmptyStateIcon() {
  return <svg aria-hidden="true" viewBox="0 0 48 48" width="40" height="40" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="21" cy="21" r="11" /><path d="M29.5 29.5 39 39" /><path d="M16 21h10" />
  </svg>;
}

export function EmptyState({ id, title, children, action, className }: { id?: string; title: string; children: ReactNode; action?: ReactNode; className?: string }) {
  return <section className={className ? `signal-empty-state ${className}` : "signal-empty-state"} aria-labelledby={id}>
    <span className="signal-empty-state__icon"><EmptyStateIcon /></span>
    <div className="signal-empty-state__body">
      <h3 id={id}>{title}</h3>
      <div className="signal-empty-state__text">{children}</div>
      {action && <div className="signal-empty-state__action">{action}</div>}
    </div>
  </section>;
}
