import { useId, useState, type ReactNode } from "react";
import { ActionLink } from "./ActionLink";
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
      <ul>{item.subIssues.map((issue, index) => <li key={`${index}:${issue}`}>{issue}</li>)}</ul>}
    {item.sources && item.sources.length > 0 && <ul>
      {item.sources.map((source, index) => <li key={`${index}:${source.label}`}>
        {source.to ? <ActionLink to={source.to} variant="quiet">{source.label}</ActionLink> : source.label}
      </li>)}
    </ul>}
  </>;
  if (!item.subIssues?.length && !item.sources?.length) return null;
  const label = item.detailsLabel ?? `${item.label} assessment details`;
  return <>
    <Button variant="quiet" aria-expanded={expanded} aria-controls={id}
      onClick={() => setExpanded((value) => !value)}>
      {expanded ? "Hide" : "Show"} {label}
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
  const sharedActions = items.flatMap(item => item.action ? [item.action] : []).filter((action, index, actions) =>
    actions.findIndex(other => other.to === action.to && other.label === action.label && other.ariaLabel === action.ariaLabel) === index
      && actions.filter(other => other.to === action.to && other.label === action.label && other.ariaLabel === action.ariaLabel).length > 1);
  const shared = (item: CoverageStatementItem) => item.action && sharedActions.some(action => action.to === item.action?.to && action.label === item.action?.label && action.ariaLabel === item.action?.ariaLabel);
  return (
    <section className="signal-review-section signal-review-section--attention signal-coverage-ledger" aria-labelledby={headingId}>
      <div className="signal-review-section__heading">
        <div>
          <p className="signal-section-kicker">{kicker}</p>
          <h2 id={headingId} tabIndex={-1}>{heading}</h2>
        </div>
        <span>{items.length} {items.length === 1 ? "limitation" : "limitations"}</span>
      </div>
      <p className="signal-review-section__intro">{intro}</p>
      <ul className="signal-coverage-list">
        {items.map((item) => (
          <li className="signal-coverage-list__item" key={item.key}>
            <p className="signal-coverage-list__label">{item.label}</p>
            <div className="signal-coverage-list__body">
              <p>{item.detail}</p>
              <CoverageDetails item={item} />
            </div>
            {item.action && !shared(item) && (
              <ActionLink className="signal-coverage-list__action" to={item.action.to} variant="secondary" aria-label={item.action.ariaLabel ?? item.action.label}>
                {item.action.label}
              </ActionLink>
            )}
          </li>
        ))}
      </ul>
      {sharedActions.length > 0 && <div className="signal-control-cluster">{sharedActions.map(action => <ActionLink key={`${action.to}:${action.label}:${action.ariaLabel ?? ""}`} to={action.to} variant="quiet" aria-label={action.ariaLabel ?? (action.label === "Open the paper" ? "Open manuscript for manual review" : action.label)}>
        {action.label === "Open the paper" ? "Open manuscript for manual review" : action.label}
      </ActionLink>)}</div>}
    </section>
  );
}
