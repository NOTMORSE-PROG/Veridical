// V-072 (F7.4), `ui-designer` spec (2026-08-20) §4.2/§4.3: the passage-
// level exploration panel — every SCORED F7.4 match (real flags, reached
// exactly like any other flag) plus, on request, the matches the DEFAULT
// policy excludes from scoring (a reference-list or block-quote passage
// on either side). Excluded matches are never scored and never a real
// Flag — shown here for the instructor's own verification only.
import { useEffect, useRef } from "react";
import { AnchorPill } from "../components/AnchorPill";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { StatusPill } from "../components/StatusPill";
import type { useExcludedReuseMatches } from "../report/useReport";
import { truncateAtWord } from "../format/text";
import type { FlagSummaryOut } from "../api/types";
import { PassagePairPanel } from "./PassagePairPanel";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function ToggleRow({
  checked,
  onChange,
  label,
  helper,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  helper: string;
}) {
  return (
    <label className="flex min-h-11 flex-col gap-0.5 px-3.5 py-2">
      <span className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4"
        />
        <span className="text-sm font-medium text-ink">{label}</span>
        <span className="text-xs text-ink-tertiary">Default: off</span>
      </span>
      <span className="pl-6 text-xs text-ink-secondary">{helper}</span>
    </label>
  );
}

export function ReuseExplorePanel({
  flags,
  onSelectFlag,
  onBack,
  includeReferenceList,
  includeBlockQuote,
  onToggleReferenceList,
  onToggleBlockQuote,
  excludedMatchesQuery,
  expandedMatchId,
  onExpandMatch,
}: {
  flags: FlagSummaryOut[] | undefined;
  onSelectFlag: (flagId: number) => void;
  onBack: () => void;
  includeReferenceList: boolean;
  includeBlockQuote: boolean;
  onToggleReferenceList: (value: boolean) => void;
  onToggleBlockQuote: (value: boolean) => void;
  excludedMatchesQuery: ReturnType<typeof useExcludedReuseMatches>;
  expandedMatchId: string | null;
  onExpandMatch: (id: string | null) => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const { data, isPending, isError, refetch } = excludedMatchesQuery;
  const scoredPassageFlags = (flags ?? []).filter(
    (f) => f.check_kind === "originality_reuse" && f.is_passage_level,
  );
  const anyToggleOn = includeReferenceList || includeBlockQuote;
  const excludedMatches = data?.matches ?? [];

  // `ux-critic` finding (2026-08-20): entering this panel (via "Explore
  // passage matches" or "See other passage matches") left focus on
  // `<body>` -- SPA-navigation focus rule (V-065's own `useRouteFocus`
  // principle, applied to this same-route panel swap): a new content
  // region replacing the analysis pane's content is functionally a
  // screen change for a keyboard/AT user even though the URL's route
  // component didn't remount.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <div className="flex flex-col">
      <div className="flex flex-col gap-2 border-b border-border px-3.5 py-3">
        <button type="button" onClick={onBack} className="w-fit text-sm font-medium text-link underline hover:text-link-hover">
          ← All flags
        </button>
        <p className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
          Originality and reuse
        </p>
        <h2 ref={headingRef} tabIndex={-1} className="text-md font-bold text-ink">
          Passage-level matches
        </h2>
      </div>

      <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
        VERIDICAL's shared originality library currently holds{" "}
        {data?.passage_archive_size_n ?? "…"} other manuscript
        {data?.passage_archive_size_n === 1 ? "" : "s"}. Coverage grows as more manuscripts are
        checked here, so an empty result here is not the same as a clean bill of health.
      </p>

      <fieldset className="border-b border-border py-1.5">
        <legend className="px-3.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase">
          Show excluded matches
        </legend>
        <ToggleRow
          checked={includeReferenceList}
          onChange={onToggleReferenceList}
          label="Include reference-list matches"
          helper="VERIDICAL excludes matches inside the reference list by default, so a shared bibliography entry doesn't get treated as reused content. Turning this on shows those matches anyway, for your own check."
        />
        <ToggleRow
          checked={includeBlockQuote}
          onChange={onToggleBlockQuote}
          label="Include block-quote matches"
          helper="VERIDICAL excludes matches inside a detected block quote by default, since a properly cited long quotation is expected to match its source. Turning this on shows those matches anyway, for your own check."
        />
      </fieldset>

      <p role="status" aria-live="polite" aria-busy={anyToggleOn && isPending} className="sr-only">
        {!anyToggleOn
          ? ""
          : isPending
            ? "Loading additional matches."
            : isError
              ? "These additional matches couldn't be loaded."
              : `${excludedMatches.length} additional match${excludedMatches.length === 1 ? "" : "es"} found.`}
      </p>

      <div>
        <p className="border-b border-border px-3.5 py-2 text-xs font-semibold tracking-header text-ink-tertiary uppercase">
          In this report's score
        </p>
        {scoredPassageFlags.length === 0 ? (
          <p className="border-b border-border px-3.5 py-3 text-sm text-ink-secondary">
            No passage-level match affected this manuscript's score.
          </p>
        ) : (
          scoredPassageFlags.map((flag) => (
            <button
              key={flag.id}
              type="button"
              onClick={() => onSelectFlag(flag.id)}
              className="flex min-h-11 w-full flex-col gap-1.5 border-b border-border px-3.5 py-3 text-left text-sm hover:bg-status-neutral-bg"
            >
              <p className={flag.overridden ? "text-ink-tertiary" : "text-ink-secondary"}>
                {truncateAtWord(flag.evidence_excerpt, 140)}
              </p>
              <div className="flex flex-wrap items-center gap-1.5">
                <AnchorPill anchor={flag.page_anchor} />
                <SeverityTag severity={flag.severity as Severity} />
                {flag.overridden && <StatusPill tone="neutral">Overridden</StatusPill>}
              </div>
            </button>
          ))
        )}
      </div>

      {anyToggleOn && (
        <div>
          <p className="border-b border-border px-3.5 py-2 text-xs font-semibold tracking-header text-ink-tertiary uppercase">
            Excluded from score, for verification only
          </p>
          {isPending && (
            <p className="flex items-center gap-1.5 px-3.5 py-3 text-sm text-ink-secondary">
              <SpinnerIcon /> Loading additional matches.
            </p>
          )}
          {isError && (
            <p role="alert" className="px-3.5 py-3 text-sm text-status-attention-text">
              These additional matches couldn't be loaded.{" "}
              <button type="button" onClick={() => refetch()} className="font-medium underline">
                Try again
              </button>
              .
            </p>
          )}
          {!isPending && !isError && excludedMatches.length === 0 && (
            <p className="px-3.5 py-3 text-sm text-ink-secondary">
              No additional matches found with the current toggles on.
            </p>
          )}
          {!isPending &&
            !isError &&
            excludedMatches.map((match) => {
              const isExpanded = expandedMatchId === match.id;
              return (
                <div key={match.id} className="border-b border-border">
                  <button
                    type="button"
                    aria-expanded={isExpanded}
                    onClick={() => onExpandMatch(isExpanded ? null : match.id)}
                    className="flex min-h-11 w-full items-center justify-between gap-2 px-3.5 py-2.5 text-left text-sm hover:bg-status-neutral-bg"
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                      <span className="text-ink-secondary">{truncateAtWord(match.own_excerpt, 140)}</span>
                      <span className="flex flex-wrap items-center gap-1.5">
                        <StatusPill tone="neutral">Not scored</StatusPill>
                        {match.excluded_reason.map((r) => (
                          <span
                            key={r}
                            className="rounded-full bg-status-neutral-bg px-2 py-0.5 text-xs text-ink-tertiary"
                          >
                            {r === "reference_list" ? "Reference list" : "Block quote"}
                          </span>
                        ))}
                      </span>
                    </span>
                    <span aria-hidden="true">{isExpanded ? "−" : "+"}</span>
                  </button>
                  {isExpanded && (
                    <div className="px-3.5 pb-3">
                      <PassagePairPanel
                        pair={match}
                        ownAnchor={
                          match.own_region.page !== null
                            ? `p. ${match.own_region.page}`
                            : match.own_region.paragraph !== null
                              ? `¶${match.own_region.paragraph}`
                              : null
                        }
                        variant="excluded"
                        excludedReason={match.excluded_reason}
                      />
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      )}
      {!anyToggleOn && (
        <p className="px-3.5 py-3 text-sm text-ink-tertiary">
          Turn on a toggle above to see matches VERIDICAL excluded by default.
        </p>
      )}
    </div>
  );
}
