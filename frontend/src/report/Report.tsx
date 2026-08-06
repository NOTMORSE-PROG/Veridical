// Screen 4h — Readiness report (F8.1-F8.2, minimal/read-only in V2:
// annotation/override arrive V-026, the decision flow V-038). Escalated
// and not_applicable/unverifiable rows are visually distinct from a real
// pass/fail (taxonomy honesty, charter rule 9) — never dressed up as a
// finding.
import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { CriterionResultOut, ReportOut } from "../api/types";
import { Chip } from "../components/Chip";
import { StatusPill, type StatusPillTone } from "../components/StatusPill";
import { useRouteFocus } from "../routing/useRouteFocus";
import { EscalatedPanel } from "./EscalatedPanel";
import { useReport } from "./useReport";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

const STATUS_TONE: Record<ReportOut["status"], StatusPillTone> = {
  ready: "success",
  conditionally_ready: "attention",
  not_ready: "attention",
  needs_review: "neutral",
};

const STATUS_LABEL: Record<ReportOut["status"], string> = {
  ready: "Ready",
  conditionally_ready: "Conditionally Ready",
  not_ready: "Not Ready",
  needs_review: "Needs Review",
};

// escalated/quota_exhausted/api_down all belong to the escalation panel
// only (backend/app/checks/escalation.py's own NEEDS_REVIEW_OUTCOMES
// grouping) — showing them here too would duplicate the same criterion
// with no action available (V-055 review: quota_exhausted/api_down rows
// were silently doing exactly that before this fix).
const NEEDS_REVIEW_OUTCOMES = new Set(["escalated", "quota_exhausted", "api_down"]);

function explainer(r: ReportOut): string {
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

function formatWeight(w: number): string {
  return `${Math.round(w * 10) / 10}%`;
}

function truncateAtWord(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut) + "…";
}

// A resolved row's source is the instructor, never the AI or the rule
// engine that originally (and unsuccessfully) tried to decide it — this
// must win over the kind-based caption unconditionally.
function sourceCaption(row: CriterionResultOut): string {
  if (row.resolution) return "Resolved by instructor";
  return row.kind === "structural" ? "Rule-checked" : "AI-graded";
}

function resultDisplay(row: CriterionResultOut): { label: string; tone: StatusPillTone; caption?: string } {
  const isPartial = row.outcome === "passed" && row.score !== null && Math.round(row.score) === 50;
  if (isPartial) {
    return { label: "Partial", tone: "attention", caption: "Counted as 50% credit toward this criterion's weight." };
  }
  switch (row.outcome) {
    case "passed":
      return { label: "Pass", tone: "success" };
    case "failed":
      return { label: "Fail", tone: "attention" };
    case "not_applicable":
      return { label: "Not applicable", tone: "neutral" };
    case "unverifiable":
      return { label: "Unverifiable", tone: "neutral", caption: "VERIDICAL could not automatically check this criterion." };
    default:
      return { label: row.outcome, tone: "neutral" };
  }
}

function AnchorPill({ anchor }: { anchor: string }) {
  return (
    <span className="w-fit rounded-full bg-status-neutral-bg px-2 py-0.5 text-xs whitespace-nowrap text-ink-tertiary">
      {anchor}
    </span>
  );
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
function ResolutionBlock({ resolution }: { resolution: NonNullable<CriterionResultOut["resolution"]> }) {
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

function EvidenceBlock({ row }: { row: CriterionResultOut }) {
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

function ResultRow({ row }: { row: CriterionResultOut }) {
  const { label, tone, caption } = resultDisplay(row);
  return (
    <>
      <div
        role="row"
        className="hidden grid-cols-[56px_minmax(0,1fr)_140px] items-start gap-3 border-t border-border px-3.5 py-3 sm:grid"
      >
        <span role="cell" className="pt-0.5 text-right text-sm whitespace-nowrap text-ink-tertiary tabular-nums">
          {formatWeight(row.weight)}
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
          <span className="text-xs text-ink-tertiary">{formatWeight(row.weight)}</span>
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

export function ReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: report, isPending, isError, error, refetch } = useReport(id);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-col gap-2 border-b border-border pb-3">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-ink-tertiary">
          <Link to="/dashboard" className="text-link underline hover:text-link-hover">
            Dashboard
          </Link>
          <span aria-hidden="true">/</span>
        </nav>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
              Readiness report
            </h1>
            {report && (
              <div className="flex flex-wrap items-center gap-1.5">
                <Chip maxWidthClass="max-w-[240px]" title={report.manuscript_group_label}>
                  {report.manuscript_group_label}
                </Chip>
                <Chip maxWidthClass="max-w-[240px]" title={report.rubric_title}>
                  {report.rubric_title}
                </Chip>
              </div>
            )}
          </div>
          {report && (
            <div className="flex flex-wrap items-center gap-3">
              <StatusPill tone={STATUS_TONE[report.status]}>{STATUS_LABEL[report.status]}</StatusPill>
              {report.composite_score !== null && (
                <span className="text-xl font-bold text-ink sm:text-2xl">{report.composite_score}%</span>
              )}
              <Link
                to={`/audit?check_run_id=${report.check_run_id}`}
                className="text-sm text-link underline hover:text-link-hover"
              >
                View audit trail
              </Link>
            </div>
          )}
        </div>
      </header>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary">
          <SpinnerIcon />
          Loading report.
        </div>
      ) : isError || !report ? (
        <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
          {error instanceof ApiError && error.code === "conflict" ? (
            <>
              This check hasn't finished yet. Its report isn't ready.{" "}
              <Link to={`/checks/${id}`} className="font-medium underline">
                View check progress
              </Link>
              .
            </>
          ) : (
            <>
              {error instanceof ApiError ? error.message : "This report couldn't be loaded."}{" "}
              <button type="button" onClick={() => refetch()} className="font-medium underline">
                Try again
              </button>
              .
            </>
          )}
        </div>
      ) : (
        <>
          <p className="rounded-lg bg-status-info-bg px-4 py-2.5 text-sm text-status-info-text">
            {explainer(report)}
          </p>

          <EscalatedPanel checkRunId={report.check_run_id} />

          {(() => {
            const mainResults = report.results.filter((r) => !NEEDS_REVIEW_OUTCOMES.has(r.outcome));
            return (
              <div role="table" aria-label="Criteria results" className="overflow-hidden rounded-lg border border-border">
                <div
                  role="row"
                  className="hidden grid-cols-[56px_minmax(0,1fr)_140px] gap-3 border-b border-border bg-status-neutral-bg px-3.5 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase sm:grid"
                >
                  <span role="columnheader" className="text-right">Wt.</span>
                  <span role="columnheader">Criterion</span>
                  <span role="columnheader" className="text-right">Result</span>
                </div>
                {mainResults.map((row) => (
                  <ResultRow key={row.criterion_id} row={row} />
                ))}
                {report.results.length === 0 && (
                  <p className="px-3.5 py-3 text-sm text-ink-tertiary">No criteria results yet.</p>
                )}
                {report.results.length > 0 && mainResults.length === 0 && (
                  <p className="px-3.5 py-3 text-sm text-ink-tertiary">
                    Every criterion is awaiting your review above.
                  </p>
                )}
              </div>
            );
          })()}
        </>
      )}
    </div>
  );
}
