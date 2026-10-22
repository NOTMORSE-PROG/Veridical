import { useId, useState, type ReactNode } from "react";
import { ActionLink } from "./ActionLink";
import { WarningTriangleIcon } from "./Alert";
import { Button } from "./Button";

export interface CoverageStatementItem {
  key: string;
  label: string;
  detail: ReactNode;
  subIssues?: string[];
  action?: { to: string; label: string; ariaLabel?: string };
  detailsLabel?: string;
  sources?: { label: string; to: string | null }[];
}

function CoverageDetails({ item }: { item: CoverageStatementItem }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const content = <>
    {item.subIssues && item.subIssues.length > 0 &&
      <ul>{item.subIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
    {item.sources && item.sources.length > 0 && <ul>
      {item.sources.map((source) => <li key={source.label}>
        {source.to ? <ActionLink to={source.to} variant="quiet">{source.label}</ActionLink> : source.label}
      </li>)}
    </ul>}
  </>;
  if (!item.detailsLabel) return content;
  return <>
    <Button variant="quiet" aria-expanded={expanded} aria-controls={id}
      onClick={() => setExpanded((value) => !value)}>
      {expanded ? "Hide" : "Show"} {item.detailsLabel}
    </Button>
    <div id={id} hidden={!expanded}>{content}</div>
  </>;
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
// content). UX review/panel review measured this live: banner blindness
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
            <p className="signal-coverage-list__label">{item.label}</p>
            <div className="signal-coverage-list__body">
              <p>{item.detail}</p>
              <CoverageDetails item={item} />
            </div>
            {item.action && (
              <ActionLink className="signal-coverage-list__action" to={item.action.to} variant="secondary" aria-label={item.action.ariaLabel ?? item.action.label}>
                {item.action.label}
              </ActionLink>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
