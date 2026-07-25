// Screen 4h — Readiness report (F8.1-F8.2, minimal/read-only in V2:
// annotation/override arrive V-026, the decision flow V-038). Escalated
// and not_applicable/unverifiable rows are visually distinct from a real
// pass/fail (taxonomy honesty, charter rule 9) — never dressed up as a
// finding.
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { CriterionResultOut, ReportOut } from "../api/types";
import { Pill, type PillStatus } from "../components/Pill";
import { EscalatedPanel } from "./EscalatedPanel";
import { useReport } from "./useReport";

const STATUS_LABELS: Record<ReportOut["status"], string> = {
  ready: "Ready",
  conditionally_ready: "Conditionally Ready",
  not_ready: "Not Ready",
  needs_review: "Needs Review",
};

const STATUS_PILL: Record<ReportOut["status"], PillStatus> = {
  ready: "ok",
  conditionally_ready: "warn",
  not_ready: "bad",
  needs_review: "queued",
};

function resultLabelAndPill(row: CriterionResultOut): { label: string; pill: PillStatus } {
  if (row.outcome === "passed" && row.score === 50) return { label: "Partial", pill: "warn" };
  switch (row.outcome) {
    case "passed":
      return { label: "Pass", pill: "ok" };
    case "failed":
      return { label: "Fail", pill: "bad" };
    case "escalated":
      return { label: "Escalated", pill: "warn" };
    case "not_applicable":
      return { label: "N/A", pill: "queued" };
    case "unverifiable":
      return { label: "Unverifiable", pill: "queued" };
    case "api_down":
      return { label: "Service unavailable", pill: "queued" };
    case "quota_exhausted":
      return { label: "Quota exhausted", pill: "queued" };
    default:
      return { label: row.outcome, pill: "queued" };
  }
}

function ResultRow({ row }: { row: CriterionResultOut }) {
  const [expanded, setExpanded] = useState(false);
  const { label, pill } = resultLabelAndPill(row);
  const explanation = row.reasoning ?? row.reason ?? null;
  const firstEvidence = row.evidence[0];
  const previewText = firstEvidence?.quote ?? explanation ?? "";
  const hasMore = row.evidence.length > 1 || (previewText.length > 140 && !expanded);

  return (
    <div className="border-t border-border-soft px-3.5 py-2.5 text-xs">
      <div className="flex items-start gap-2.5">
        <span className="w-8 flex-none text-ink-faint">{row.weight}%</span>
        <span className="flex-1 text-ink">{row.text}</span>
        <Pill status={pill}>{label}</Pill>
      </div>
      {previewText && (
        <div className="mt-1.5 ml-8 flex flex-col gap-1">
          <p className="text-ink-soft">
            {expanded ? previewText : previewText.slice(0, 140) + (previewText.length > 140 ? "…" : "")}
          </p>
          {(firstEvidence?.anchor ?? row.anchor) && (
            <span className="w-fit rounded-pill bg-page px-2 py-0.5 text-2xs text-ink-faint">
              {firstEvidence?.anchor ?? row.anchor}
            </span>
          )}
          {(hasMore || row.evidence.length > 1) && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="w-fit text-2xs text-primary hover:underline"
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
          {expanded &&
            row.evidence.slice(1).map((ev, i) => (
              <div key={i} className="flex flex-col gap-0.5 border-t border-border-soft pt-1">
                <p className="text-ink-soft">{ev.quote}</p>
                <span className="w-fit rounded-pill bg-page px-2 py-0.5 text-2xs text-ink-faint">
                  {ev.anchor}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

export function ReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: report, isLoading, error } = useReport(id);

  if (isLoading) {
    return <div className="p-4 text-xs text-ink-faint">Loading report…</div>;
  }
  if (error) {
    const message =
      error instanceof ApiError ? error.message : "This report couldn't be loaded.";
    return (
      <div className="p-4 text-xs text-status-bad-text" role="alert">
        {message}
      </div>
    );
  }
  if (!report) return null;

  const decidedResults = report.results.filter((row) => row.outcome !== "escalated");

  const thresholdExplainer =
    report.status === "ready"
      ? `Ready because the score is ${report.composite_score}% (≥ ${report.thresholds.ready_min_score}%) with no unresolved high-severity flags.`
      : report.status === "not_ready"
        ? `Not Ready because the score is ${report.composite_score}% (< ${report.thresholds.not_ready_max_score}%) or an unresolved high-severity flag exists.`
        : report.status === "needs_review"
          ? (report.reason ?? "This run needs manual review.")
          : `Conditionally Ready — the score is between ${report.thresholds.not_ready_max_score}% and ${report.thresholds.ready_min_score}%.`;

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2.5 border-b border-border-soft pb-2">
        <div>
          <div className="text-lg font-bold text-ink">Readiness report</div>
          <div className="text-xs text-ink-faint">
            {report.manuscript_group_label} · {report.rubric_title}
          </div>
        </div>
        <span className="flex-1" />
        <Pill status={STATUS_PILL[report.status]}>{STATUS_LABELS[report.status]}</Pill>
        {report.composite_score !== null && (
          <span className="text-lg font-bold text-ink">{report.composite_score}%</span>
        )}
        <Link
          to={`/audit?check_run_id=${report.check_run_id}`}
          className="text-xs text-primary hover:underline"
        >
          View audit trail
        </Link>
      </div>

      <p className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text">
        {thresholdExplainer}
      </p>

      <EscalatedPanel checkRunId={report.check_run_id} />

      <div className="rounded-panel border border-border">
        <div className="flex items-center gap-2.5 border-b border-border-soft bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header text-table-head uppercase">
          <span className="w-8 flex-none">Wt.</span>
          <span className="flex-1">Criterion</span>
          <span>Result</span>
        </div>
        {/* Escalated rows have their own dedicated resolution UI above
            (EscalatedPanel) — shown here too would duplicate the same
            criterion with no action available, so they're skipped until
            resolved, at which point they reappear here as a normal row. */}
        {decidedResults.map((row) => (
          <ResultRow key={row.criterion_id} row={row} />
        ))}
        {report.results.length === 0 && (
          <p className="px-3.5 py-3 text-xs text-ink-faint">No criteria results yet.</p>
        )}
        {report.results.length > 0 && decidedResults.length === 0 && (
          <p className="px-3.5 py-3 text-xs text-ink-faint">
            Every criterion is awaiting your review above.
          </p>
        )}
      </div>
    </div>
  );
}
