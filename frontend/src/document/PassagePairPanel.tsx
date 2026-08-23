// V-072 (F7.4), `ui-designer` spec (2026-08-20) §4.4: the two-sided
// passage comparison. Own passage on one side, matched archive passage
// on the other, both showing real text -- the similarity score stays
// subordinate (owner's anti-Turnitin ruling, carried from V-058), never
// a large colored percentage or a severity-styled badge.
import { Link } from "react-router";
import type { PassagePairOut } from "../api/types";

const HIGHLIGHT_STYLE = {
  background: "color-mix(in srgb, var(--color-status-caution-text) 20%, transparent)",
};

// V-066: exported so the library's bounded-excerpt view (a non-owned
// manuscript's detail/compare pane) can reuse the identical block
// primitive instead of a second, near-identical one.
export function PassageBlock({
  label,
  before,
  excerpt,
  after,
}: {
  label: string;
  before: string | null;
  excerpt: string;
  after: string | null;
}) {
  return (
    <div className="min-w-0 flex-1">
      <p className="mb-1 text-xs font-semibold tracking-header text-ink-tertiary uppercase">{label}</p>
      <div className="rounded-lg border border-border bg-page px-4 py-3 text-sm break-words text-ink">
        {before && <span className="text-ink-tertiary">{before} </span>}
        <span style={HIGHLIGHT_STYLE}>{excerpt}</span>
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
  const pct = Math.round(pair.similarity * 100 * 10) / 10;

  const excludedLead = (() => {
    if (excludedReason.length === 0) return null;
    const label =
      excludedReason.length === 2
        ? "the reference list and a detected block quote"
        : excludedReason[0] === "reference_list"
          ? "the reference list"
          : "a detected block quote";
    return `This passage was excluded from scoring because it falls inside ${label}. Shown here for your own check, not as a finding.`;
  })();

  return (
    <section aria-label="Passage comparison" className="flex flex-col gap-3">
      <h3 className="sr-only" tabIndex={-1}>
        Passage comparison
      </h3>
      <p className="text-sm text-ink-secondary">
        {variant === "flag"
          ? "Possible reuse. Compare the two passages below and verify against the matched source yourself."
          : excludedLead}
      </p>
      <p className="text-xs text-ink-secondary">{pct}% textual similarity to the passage below.</p>
      <div className="flex flex-col gap-3 xl:flex-row">
        <PassageBlock
          label={ownAnchor ? `Your manuscript · ${ownAnchor}` : "Your manuscript"}
          before={pair.own_context_before}
          excerpt={pair.own_excerpt}
          after={pair.own_context_after}
        />
        <div className="min-w-0 flex-1">
          <PassageBlock
            label={`Archived manuscript #${pair.matched_ref}`}
            before={pair.matched_context_before}
            excerpt={pair.matched_excerpt}
            after={pair.matched_context_after}
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
