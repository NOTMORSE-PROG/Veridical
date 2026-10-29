// V-072 (F7.4), design review spec (2026-08-20) §4.4: the two-sided
// passage comparison. Own passage on one side, matched archive passage
// on the other, both showing real text -- the similarity score stays
// subordinate (owner's anti-Turnitin ruling, carried from V-058), never
// a large colored percentage or a severity-styled badge.
import { ActionLink } from "../ui/ActionLink";
import type { ReactNode } from "react";
import type { PassagePairOut } from "../api/types";
import type { TextRange } from "./sharedPassage";
import { sharedPassageRanges } from "./sharedPassage";

export function HighlightedText({ text, ranges }: { text: string; ranges: TextRange[] }) {
  if (ranges.length === 0) return <>{text}</>;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start > cursor) nodes.push(text.slice(cursor, range.start));
    nodes.push(<mark key={`${range.start}-${range.end}`} className="reuse-shared-wording">{text.slice(range.start, range.end)}</mark>);
    cursor = range.end;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return <>{nodes}</>;
}

// V-066: exported so the library's bounded-excerpt view (a non-owned
// manuscript's detail/compare pane) can reuse the identical block
// primitive instead of a second, near-identical one.
export function PassageBlock({
  label,
  before,
  excerpt,
  after,
  sharedRanges = [],
}: {
  label: string;
  before: string | null;
  excerpt: string;
  after: string | null;
  sharedRanges?: TextRange[];
}) {
  return (
    <div className="passage-pair__block">
      <p className="mb-1 text-xs font-semibold tracking-header text-ink-tertiary uppercase">{label}</p>
      {/* Long excerpts scroll inside the review pane, so the box must be reachable by keyboard. */}
      <div className="passage-pair__text rounded-lg border border-border bg-page px-4 py-3 break-words text-ink" tabIndex={0} role="region" aria-label={label}>
        {before && <span className="text-ink-tertiary">{before} </span>}
        <span><HighlightedText text={excerpt} ranges={sharedRanges} /></span>
        {after && <span className="text-ink-tertiary"> {after}</span>}
      </div>
    </div>
  );
}

export function PassagePairPanel({
  pair,
  ownAnchor,
  variant,
  excludedReason = [],
  placement = "full-page",
  wholeManuscript = false,
}: {
  pair: PassagePairOut;
  ownAnchor: string | null;
  variant: "flag" | "excluded";
  excludedReason?: ("reference_list" | "block_quote")[];
  placement?: "inspector" | "full-page";
  wholeManuscript?: boolean;
}) {
  const shared = sharedPassageRanges(pair.own_excerpt, pair.matched_excerpt);
  const hasExactSharedWording = shared.left.length > 0;

  const excludedLead = (() => {
    if (variant !== "excluded" || excludedReason.length === 0) return null;
    const label =
      excludedReason.length === 2
        ? "the reference list and a detected block quote"
        : excludedReason[0] === "reference_list"
          ? "the reference list"
          : "a detected block quote";
    return `This passage was not included in readiness because it falls inside ${label}. Shown here for your own check, not as a finding.`;
  })();

  return (
    <section aria-label="Passage comparison" className={`passage-pair passage-pair--${placement} flex flex-col gap-3`}>
      <h3 className="sr-only" tabIndex={-1}>
        Passage comparison
      </h3>
      {excludedLead && <p className="signal-reading-note__lead">{excludedLead}</p>}
      <div className="signal-reading-note">
        <p className="signal-reading-note__lead">Compare the stored passages in context. Similar wording does not establish identical passages or files, copying, authorship or which document came first.</p>
        <ul>
          <li>{hasExactSharedWording ? "Shaded wording appears in both stored passages." : "No stable exact phrase was found to highlight. Compare both passages manually."}</li>
          <li>These are stored excerpts, not complete source documents.</li>
        </ul>
        {ownAnchor && <p className="signal-reading-note__meta"><strong>Recorded {wholeManuscript ? "scope" : "location"}</strong> {ownAnchor}</p>}
      </div>
      <div className="passage-pair__columns">
        <PassageBlock
          label="Your manuscript excerpt"
          before={pair.own_context_before}
          excerpt={pair.own_excerpt}
          after={pair.own_context_after}
          sharedRanges={shared.left}
        />
        <div className="passage-pair__block">
          <PassageBlock
            label={`Stored comparison excerpt · Library record #${pair.matched_ref}`}
            before={pair.matched_context_before}
            excerpt={pair.matched_excerpt}
            after={pair.matched_context_after}
            sharedRanges={shared.right}
          />
          {/* V-066 (BUG-122 direction 2): "#N" used to be dead information
              -- the library now gives it somewhere real to go. */}
          <ActionLink to={`/library/${pair.matched_ref}`} variant="secondary" className="passage-pair__library-link" aria-label={`Open in Library: record #${pair.matched_ref}`}>Open in Library</ActionLink>
        </div>
      </div>
    </section>
  );
}
