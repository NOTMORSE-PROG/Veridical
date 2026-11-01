import type { ReactNode } from "react";

/** One quoted piece of evidence, laid out the same on every review screen:
 * an optional label, the quote with its location, any notes, one row of
 * actions, then whatever an action shows (a preview or a message) across the
 * full width. Layout only; the caller owns any state. */
export function EvidenceItem({ label, quote, missing, anchor, notes, actions, result }: {
  label?: string;
  /** Empty when nothing was quoted: `missing` is said instead, never in quotation marks. */
  quote: string;
  missing?: string;
  anchor?: string;
  notes?: ReactNode;
  actions?: ReactNode;
  result?: ReactNode;
}) {
  return <div className="signal-evidence-item">
    {label && <p className="signal-evidence-item__label">{label}</p>}
    {quote.trim() ? <blockquote className="signal-evidence-item__quote"><span>“{quote}”</span>{anchor && <cite>{anchor}</cite>}</blockquote>
      : <p className="signal-evidence-item__note">{missing ?? "No quotation was saved."}{anchor && <> ({anchor})</>}</p>}
    {notes}
    {actions && <div className="signal-evidence-item__actions">{actions}</div>}
    {result && <div className="signal-evidence-item__result">{result}</div>}
  </div>;
}
