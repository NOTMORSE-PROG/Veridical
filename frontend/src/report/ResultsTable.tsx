// Extracted out of Report.tsx (V-040) so the public adviser view
// (AdviserView.tsx) can render the exact same criteria table an
// instructor sees, read-only, from the same source of truth -- one
// place decides what a result row looks like, not two independently
// drifting copies.
import type { ReactNode } from "react";
import { useState } from "react";
import type { ReportCommon, ResolutionOut, ResultRowCommon } from "../api/types";
import { AnchorPill } from "../components/AnchorPill";
import { StatusPill, type StatusPillTone } from "../components/StatusPill";
import { WeightImportanceTag } from "../components/WeightImportanceTag";
import { truncateAtWord } from "../format/text";

// escalated/quota_exhausted/api_down all belong to the escalation panel
// only (backend/app/checks/escalation.py's own NEEDS_REVIEW_OUTCOMES
// grouping) — showing them here too would duplicate the same criterion
// with no action available (V-055 review: quota_exhausted/api_down rows
// were silently doing exactly that before this fix).
export const NEEDS_REVIEW_OUTCOMES = new Set(["escalated", "quota_exhausted", "api_down"]);

// BUG-044: typed against ReportCommon, not ReportOut -- this function is
// called from the public adviser view (AdviserView.tsx) with a
// PublicReportOut, which does not have every field ReportOut does.
function explainerBase(r: ReportCommon): string {
  if (r.status === "needs_review") return r.reason ?? "This run needs manual review.";
  if (r.unresolved_high_flag_count > 0) {
    return `Not Ready. ${r.unresolved_high_flag_count} unresolved high-severity ${r.unresolved_high_flag_count === 1 ? "flag" : "flags"} found. Any unresolved high-severity flag forces Not Ready, regardless of score.`;
  }
  if (r.status === "ready") {
    return `Ready. The score is ${r.composite_score}% (at or above the ${r.thresholds.ready_min_score}% floor) with no unresolved high-severity flags.`;
  }
  if (r.status === "not_ready") {
    return `Not Ready. The score is ${r.composite_score}% (below the ${r.thresholds.not_ready_max_score}% floor).`;
  }
  return `Conditionally Ready. The score is ${r.composite_score}%, between ${r.thresholds.not_ready_max_score}% and ${r.thresholds.ready_min_score}%.`;
}

// BUG-033: the explainer must honestly account for WHERE a deduction
// came from — before this, a run could lose real points to unresolved
// flags with no mention anywhere on the page of why. `flagsAnchor`
// renders a real same-page anchor to the flags panel below when one
// exists on this page (the instructor's own report screen); the public
// adviser view passes `null` since it has no such anchor to jump to
// yet, and says so in plain prose instead of a dead link.
export function explainer(r: ReportCommon, flagsAnchor: string | null): ReactNode {
  const base = explainerBase(r);
  if (r.flag_deduction <= 0) return base;
  const points = Math.round(r.flag_deduction * 10) / 10;
  if (!flagsAnchor) {
    return `${base} This includes a ${points}-point deduction from unresolved flags. See Flags below.`;
  }
  return (
    <>
      {base} This includes a {points}-point deduction from unresolved flags. See{" "}
      <a href={flagsAnchor} className="font-medium underline">
        Flags
      </a>{" "}
      below.
    </>
  );
}

// A resolved row's source is the instructor, never the AI or the rule
// engine that originally (and unsuccessfully) tried to decide it — this
// must win over the kind-based caption unconditionally. Exported (BUG-082)
// so `ResultsTable.contract.test.ts` can assert this agrees with the
// backend PDF's `_source_caption` against the same shared fixture cases
// (`tests/fixtures/source_caption_cases.json`) -- the missing test that
// let the two implementations disagree unnoticed in the first place.
export function sourceCaption(row: ResultRowCommon): string {
  if (row.resolution) return "Resolved by instructor";
  return row.kind === "structural" ? "Rule-checked" : "AI-graded";
}

// `attention` is system/process state only, never a manuscript verdict
// (tokens.css, V-056's §1.2 rule) — found live by `ux-critic`, this
// function was the one place that rule wasn't actually followed: a
// criterion Fail is a real finding against the manuscript's own content,
// not a routine hiccup, and was rendering pixel-identical to "Ingestion
// failed." Fail moves to `danger` (a definitive negative finding, same
// weight as the composite Not Ready verdict); Partial moves to
// `caution` (the ambiguous middle case caution exists for — half credit
// is neither a clean pass nor a clean fail).
//
// ui-designer finding (V-040): the `default:` branch below was dead code
// on the instructor's own screen (escalated/quota_exhausted/api_down
// rows never reach it there — Report.tsx filters them into a separate
// panel first) but becomes live the moment the public adviser view
// passes the FULL, unfiltered results array (no separate escalation
// panel exists on that read-only page). Real cases now, not a silent
// raw-enum fallback -- an adviser must never see a bare "escalated"
// string with no explanation.
export function resultDisplay(
  row: ResultRowCommon,
): { label: string; tone: StatusPillTone; caption?: string } {
  const isPartial = row.outcome === "passed" && row.score !== null && Math.round(row.score) === 50;
  if (isPartial) {
    return {
      label: "Partial",
      tone: "caution",
      caption: "Counted as 50% credit toward this criterion's weight.",
    };
  }
  switch (row.outcome) {
    case "passed":
      return { label: "Pass", tone: "success" };
    case "failed":
      return { label: "Fail", tone: "danger" };
    case "not_applicable":
      return { label: "Not applicable", tone: "neutral" };
    case "unverifiable":
      return {
        label: "Unverifiable",
        tone: "neutral",
        caption: "VERIDICAL could not automatically check this criterion.",
      };
    case "escalated":
      return {
        label: "Awaiting review",
        tone: "neutral",
        caption: "The instructor has not resolved this criterion yet.",
      };
    case "quota_exhausted":
      return {
        label: "Awaiting review",
        tone: "neutral",
        caption:
          "VERIDICAL couldn't grade this criterion today. The instructor will resolve it manually or re-run later.",
      };
    case "api_down":
      return {
        label: "Awaiting review",
        tone: "neutral",
        caption:
          "VERIDICAL couldn't reach its AI service to grade this criterion. The instructor will resolve it manually or re-run later.",
      };
    default:
      return { label: row.outcome, tone: "neutral" };
  }
}

const RESOLUTION_VERB: Record<string, string> = {
  accept_majority: "Accepted the AI's verdict",
  mark_pass: "Marked Pass",
  mark_fail: "Marked Fail",
};

// A resolved criterion must never render as an ordinary AI-graded/rule-
// checked row: the AI's own (superseded) text stayed in `reason`/
// `reasoning` unchanged, and without checking `resolution` FIRST it
// silently outranked the instructor's own decision (V-055 review — the
// exact failure this system's human-in-the-loop design exists to
// prevent, ground rule 1).
function ResolutionBlock({ resolution }: { resolution: ResolutionOut }) {
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <p className="text-ink">
        <span className="font-semibold">{RESOLUTION_VERB[resolution.type] ?? "Resolved by instructor"}.</span>{" "}
        {resolution.reason}
      </p>
      {resolution.ai_majority_verdict && (
        <p className="text-xs text-ink-tertiary">
          The AI's own vote leaned {resolution.ai_majority_verdict}, but did not reach the agreement
          threshold needed to auto-decide.
        </p>
      )}
    </div>
  );
}

function EvidenceBlock({ row }: { row: ResultRowCommon }) {
  const [expanded, setExpanded] = useState(false);
  const explanation = row.reasoning ?? row.reason ?? null;
  const first = row.evidence[0];
  const controlsId = `evidence-more-${row.criterion_id}`;

  if (row.resolution) {
    return <ResolutionBlock resolution={row.resolution} />;
  }
  if (row.evidence.length > 0) {
    const text = first.quote;
    const shown = expanded ? text : truncateAtWord(text, 140);
    return (
      <div className="flex flex-col gap-1.5 text-sm">
        <p className="text-ink-secondary">{shown}</p>
        <AnchorPill anchor={first.anchor} />
        {(row.evidence.length > 1 || text.length > 140) && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={controlsId}
            onClick={() => setExpanded((v) => !v)}
            className="w-fit text-sm font-medium text-link underline hover:text-link-hover"
          >
            {expanded ? "Show less" : "Show more"}
          </button>
        )}
        {expanded && (
          <div id={controlsId} className="flex flex-col gap-2">
            {row.evidence.slice(1).map((ev, i) => (
              <div key={i} className="flex flex-col gap-1 border-t border-border pt-1.5">
                <p className="text-ink-secondary">{ev.quote}</p>
                <AnchorPill anchor={ev.anchor} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (explanation) {
    return (
      <div className="flex flex-col gap-1.5 text-sm">
        <p className="text-ink-secondary">{explanation}</p>
        {row.anchor && <AnchorPill anchor={row.anchor} />}
      </div>
    );
  }
  if (row.anchor) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-ink-tertiary">
        Verified at <AnchorPill anchor={row.anchor} />
      </p>
    );
  }
  return null;
}

function ResultRow({ row }: { row: ResultRowCommon }) {
  const { label, tone, caption } = resultDisplay(row);
  return (
    <>
      <div
        role="row"
        className="hidden grid-cols-[88px_minmax(0,1fr)_140px] items-start gap-3 border-t border-border px-3.5 py-3 sm:grid"
      >
        <span role="cell" className="pt-0.5">
          <WeightImportanceTag importance={row.weight_importance} />
        </span>
        <div role="cell" className="min-w-0">
          <p className="text-sm text-ink">{row.text}</p>
          <p className="mt-0.5 text-xs text-ink-tertiary">
            {sourceCaption(row)}
            {caption ? ` · ${caption}` : ""}
          </p>
          <div className="mt-1.5">
            <EvidenceBlock row={row} />
          </div>
        </div>
        <div role="cell" className="flex justify-end">
          <StatusPill tone={tone}>{label}</StatusPill>
        </div>
      </div>

      <div className="border-t border-border p-3 sm:hidden">
        <div className="flex items-start justify-between gap-2">
          <WeightImportanceTag importance={row.weight_importance} />
          <StatusPill tone={tone}>{label}</StatusPill>
        </div>
        <p className="mt-1 text-sm text-ink">{row.text}</p>
        <p className="mt-0.5 text-xs text-ink-tertiary">
          {sourceCaption(row)}
          {caption ? ` · ${caption}` : ""}
        </p>
        <div className="mt-1.5">
          <EvidenceBlock row={row} />
        </div>
      </div>
    </>
  );
}

/** The reference-only criteria table (screen 4h, and the read-only
 * adviser view, V-040). `results` is whatever the caller wants shown --
 * Report.tsx passes the NEEDS_REVIEW_OUTCOMES-filtered subset (it has a
 * separate EscalatedPanel for those); AdviserView.tsx passes the full,
 * unfiltered array (it has no separate panel, so `resultDisplay()`'s own
 * "Awaiting review" cases carry that state instead). */
export function ResultsTable({
  results,
  totalCount,
  awaitingReviewMessage = "Every criterion is awaiting your review above.",
}: {
  results: ResultRowCommon[];
  totalCount: number;
  /** Report.tsx (instructor, "your review" -- the escalation panel
   * above is theirs to act on) and AdviserView.tsx (read-only, no
   * panel to point at) need different wording for the same state. */
  awaitingReviewMessage?: string;
}) {
  return (
    <div
      role="table"
      aria-label="Criteria results"
      className="overflow-hidden rounded-lg border border-border/60"
    >
      <div
        role="row"
        className="hidden grid-cols-[88px_minmax(0,1fr)_140px] gap-3 border-b border-border bg-status-neutral-bg px-3.5 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase sm:grid"
      >
        <span role="columnheader">Importance</span>
        <span role="columnheader">Criterion</span>
        <span role="columnheader" className="text-right">
          Result
        </span>
      </div>
      {results.map((row) => (
        <ResultRow key={row.criterion_id} row={row} />
      ))}
      {totalCount === 0 && <p className="px-3.5 py-3 text-sm text-ink-tertiary">No criteria results yet.</p>}
      {totalCount > 0 && results.length === 0 && (
        <p className="px-3.5 py-3 text-sm text-ink-tertiary">{awaitingReviewMessage}</p>
      )}
    </div>
  );
}
