// V-072 (F7.4), `ui-designer` spec (2026-08-20) §4.4: the two-sided
// passage comparison. Own passage on one side, matched archive passage
// on the other, both showing real text -- the similarity score stays
// subordinate (owner's anti-Turnitin ruling, carried from V-058), never
// a large colored percentage or a severity-styled badge.
import { Link } from "react-router";
import type { ReactNode } from "react";
import type { PassagePairOut } from "../api/types";
import type { TextRange } from "./sharedPassage";
import { sharedPassageRanges } from "./sharedPassage";

function HighlightedText({ text, ranges }: { text: string; ranges: TextRange[] }) {
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
    <div className="min-w-0 flex-1">
      <p className="mb-1 text-xs font-semibold tracking-header text-ink-tertiary uppercase">{label}</p>
      <div className="rounded-lg border border-border bg-page px-4 py-3 text-sm break-words text-ink">
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
}: {
  pair: PassagePairOut;
  ownAnchor: string | null;
  variant: "flag" | "excluded";
  excludedReason?: ("reference_list" | "block_quote")[];
}) {
  const similarityBand = pair.level === "exact_duplicate" ? "Exact duplicate" : "High textual similarity";
  const shared = sharedPassageRanges(pair.own_excerpt, pair.matched_excerpt);
  const hasExactSharedWording = shared.left.length > 0;

  const excludedLead = (() => {
    if (excludedReason.length === 0) return null;
    const label =
      excludedReason.length === 2
        ? "the reference list and a detected block quote"
        : excludedReason[0] === "reference_list"
          ? "the reference list"
          : "a detected block quote";
    return `This passage was not included in readiness because it falls inside ${label}. Shown here for your own check, not as a finding.`;
  })();

  return (
    <section aria-label="Passage comparison" className="flex flex-col gap-3">
      <h3 className="sr-only" tabIndex={-1}>
        Passage comparison
      </h3>
      <p className="text-sm text-ink-secondary">
        {variant === "flag"
          ? "Compare these stored passages yourself. Similar wording can have legitimate explanations."
          : excludedLead}
      </p>
      <p className="text-xs text-ink-secondary"><b>Match type: {similarityBand}.</b> Compare the text itself below; the raw reproducibility value remains in Audit.</p>
      <p className="text-xs text-ink-secondary">
        {hasExactSharedWording
          ? "Highlighted wording appears in both recorded passages. Unhighlighted wording still matters to the comparison."
          : "No stable exact phrase was found to highlight. The stored match may reflect broader textual similarity, so compare both passages manually."}
      </p>
      <div className="passage-pair__columns flex flex-col gap-3 xl:flex-row">
        <PassageBlock
          label={ownAnchor ? `Your manuscript · ${ownAnchor}` : "Your manuscript"}
          before={pair.own_context_before}
          excerpt={pair.own_excerpt}
          after={pair.own_context_after}
          sharedRanges={shared.left}
        />
        <div className="min-w-0 flex-1">
          <PassageBlock
            label={`Archived manuscript #${pair.matched_ref}`}
            before={pair.matched_context_before}
            excerpt={pair.matched_excerpt}
            after={pair.matched_context_after}
            sharedRanges={shared.right}
          />
          <p className="mt-1 text-xs text-ink-tertiary">Stored excerpt, not the full document.</p>
          {/* V-066 (BUG-122 direction 2): "#N" used to be dead information
              -- the library now gives it somewhere real to go. */}
          <Link
            to={`/library/${pair.matched_ref}`}
            className="mt-1 inline-block text-xs font-medium text-link underline hover:text-link-hover"
          >
            Open in Library
          </Link>
        </div>
      </div>
      <p className="text-xs text-ink-tertiary">
        Showing {pair.context_words_each_side} words of context on each side of the matched passage.
        This is not the full archived document.
      </p>
    </section>
  );
}
