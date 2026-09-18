import type { ReactNode } from "react";
import { ActionLink } from "./ActionLink";
import { WarningTriangleIcon } from "./Alert";

export interface CoverageStatementItem {
  key: string;
  label: string;
  detail: ReactNode;
  subIssues?: string[];
  action?: { to: string; label: string; ariaLabel?: string };
}

interface CoverageStatementProps {
  // DESIGN.md §8 names four states for this primitive (complete, partial,
  // unresolved, unavailable); only "partial" has a real caller today
  // (BUG-127) -- the other three stay reserved for whichever ticket first
  // needs them, rather than guessed at now.
  state: "partial";
  headingId: string;
  kicker?: string;
  heading: string;
  intro: string;
  items: CoverageStatementItem[];
}

// BUG-127: the report used to stack one `Alert` per partial-assessment
// gap (required-format warning, internal agreement, citation integrity --
// up to 3-4 identically-shaped colored boxes before any real report
// content). `ux-critic`/`professor` measured this live: banner blindness
// by the second box, and DESIGN.md §8/§11 already specified this exact
// primitive as the fix -- it had never been built. Reuses the same
// `.signal-review-section` rhythm already used three times lower on this
// page (`SignalReviewSections.tsx`) instead of adding a fourth visual
// language, and moves the accent to the section's own left border so the
// page reads as "one thing to notice, detail one glance further in"
// rather than N separately-competing alerts.
export function CoverageStatement({ headingId, kicker = "Assessment coverage", heading, intro, items }: CoverageStatementProps) {
  if (items.length === 0) return null;
  return (
    <section className="signal-review-section signal-review-section--attention" aria-labelledby={headingId}>
      <div className="signal-review-section__heading">
        <div>
          <p className="signal-section-kicker">{kicker}</p>
          <h2 id={headingId}>{heading}</h2>
        </div>
        <span>{items.length} {items.length === 1 ? "limitation" : "limitations"}</span>
      </div>
      <p className="signal-review-section__intro">{intro}</p>
      <ul className="signal-coverage-list">
        {items.map((item) => (
          <li className="signal-coverage-list__item" key={item.key}>
            <WarningTriangleIcon />
            <div className="signal-coverage-list__body">
              <p className="signal-coverage-list__label">{item.label}</p>
              <p>{item.detail}</p>
              {item.subIssues && item.subIssues.length > 0 && (
                <ul>{item.subIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              )}
            </div>
            {item.action && (
              <ActionLink to={item.action.to} variant="secondary" aria-label={item.action.ariaLabel ?? item.action.label}>
                {item.action.label}
              </ActionLink>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
