import { type MouseEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { type CoverageStatementItem, CoverageStatement } from "../ui/CoverageStatement";
import { ReadinessBand } from "../ui/ReadinessBand";
import { SignalDecisionPanel } from "./SignalDecisionPanel";
import { SignalCriteriaResults, SignalEscalatedPanel, SignalFlagsPanel } from "./SignalReviewSections";
import { SignalShareDialog } from "./SignalShareDialog";
import { useExportReportPdf, useReport } from "./useReport";
import { useShareLink } from "./useShare";

const INTEGRITY_LABEL: Record<IntegrityCheckStatusOut["check_kind"], string> = {
  internal_agreement: "Internal agreement",
  citation_integrity: "Citation integrity",
};

// BUG-127/BUG-128: this used to be one `Alert` per gap (up to 3-4 stacked,
// identically-shaped colored boxes -- banner blindness, `ux-critic`/
// `professor` both measured it live) and each one named a gap with no way
// to close it (Norman's gulf of execution). Consolidated into the single
// `CoverageStatement` DESIGN.md §8/§11 already specified for exactly this,
// and each item now links to the real, existing remedy: the required-
// format review screen, or the same "Run again" rerun mechanism the
// Dashboard row action already uses (BUG-128's own fix, `manuscript_id`
// newly threaded onto `ReportOut` for this).
function integrityCoverageItem(status: IntegrityCheckStatusOut, manuscriptId: number): CoverageStatementItem {
  const unavailable = status.n_skipped_api_down;
  const capacity = status.n_skipped_quota;
  const parse = status.n_skipped_parse_failure;
  const outcomeText =
    status.outcome === "api_down"
      ? "a service interruption"
      : status.outcome === "quota_exhausted"
        ? "a free-capacity limit"
        : "an unverifiable result";
  const subIssues: string[] = [];
  if (unavailable > 0) subIssues.push(`${unavailable} item${unavailable === 1 ? "" : "s"} skipped because a service was unavailable.`);
  if (capacity > 0) subIssues.push(`${capacity} item${capacity === 1 ? "" : "s"} skipped because daily AI capacity was spent.`);
  if (parse > 0) subIssues.push(`${parse} item${parse === 1 ? "" : "s"} skipped because the source could not be parsed reliably.`);
  const label = INTEGRITY_LABEL[status.check_kind];
  return {
    key: status.check_kind,
    label,
    detail: `The check recorded ${outcomeText}. Nothing skipped is presented as passed.`,
    subIssues,
    action: {
      to: `/dashboard?rerun=${manuscriptId}`,
      label: "Run again",
      // `ux-critic` (BUG-127 review): WCAG 2.5.3 Label in Name requires
      // the accessible name to CONTAIN the visible text as a substring --
      // "Run the X check again" doesn't, since "Run" and "again" aren't
      // contiguous. Fixed to lead with the visible label verbatim, same
      // shape as this codebase's own correct precedent two sections down
      // (`SignalReviewSections.tsx`'s "Review evidence and reasoning: ${row.text}").
      ariaLabel: `Run again: ${label.toLowerCase()} check for this manuscript`,
    },
  };
}

function coverageItems(report: ReportOut): CoverageStatementItem[] {
  const items: CoverageStatementItem[] = [];
  if (report.rubric_needs_review) {
    items.push({
      key: "rubric_needs_review",
      label: "Required format review",
      detail: "The required format was activated with unresolved parser uncertainty. Check the criterion record against the original document.",
      subIssues: report.rubric_parse_issues ?? undefined,
      action: { to: "/rubric", label: "Review required format" },
    });
  }
  for (const status of report.integrity_check_status ?? []) {
    items.push(integrityCoverageItem(status, report.manuscript_id));
  }
  return items;
}

// `scrollIntoView` defaults to false: the existing plain `<a href="#...">`
// jump-nav links below are same-page hash anchors the BROWSER's own
// native navigation already scrolls to before this handler runs --
// BUG-158 deliberately used `preventScroll: true` here to avoid a second,
// conflicting scroll jump on top of that native one. The hero KPI link
// (`ux-critic` finding, BUG-167 review, live-reproduced: `window.scrollY`
// stayed 0 after clicking it) is different -- it's a react-router `Link`
// whose href changes the SEARCH string, not just the hash, so react-router
// intercepts the click and handles the whole navigation itself; no native
// browser anchor-scroll ever happens for it. `scrollIntoView: true` opts
// that one link into an explicit scroll instead of assuming one already
// occurred.
function focusReportJumpTarget(
  event: MouseEvent<HTMLAnchorElement>,
  { scrollIntoView = false }: { scrollIntoView?: boolean } = {},
) {
  const targetId = decodeURIComponent(event.currentTarget.hash.slice(1));
  requestAnimationFrame(() => {
    const target = document.getElementById(targetId);
    if (!target) return;
    if (scrollIntoView) target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  });
}

export function SignalReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: report, isPending, isError, error, refetch } = useReport(id);
  const exportPdf = useExportReportPdf(id);
  const { data: shareLink } = useShareLink(id);
  const [shareOpen, setShareOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const exportErrorRef = useRef<HTMLDivElement>(null);
  const exportInFlight = useRef(false);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);

  useEffect(() => {
    if (exportPdf.isError) exportErrorRef.current?.focus();
  }, [exportPdf.isError]);

  function exportReport() {
    if (exportInFlight.current) return;
    exportInFlight.current = true;
    exportPdf.mutate(undefined, { onSettled: () => { exportInFlight.current = false; } });
  }

  const identity = report ? manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename) : undefined;
  const hasReportNotices = report && (
    report.previous_status
    || report.llm_mode !== "real"
    || report.rubric_needs_review
    || (report.integrity_check_status ?? []).length > 0
  );

  return (
    <div className="signal-route signal-page-flow signal-report">
      <header className="signal-route-header signal-report-header">
        <div>
          <nav aria-label="Breadcrumb"><Link to="/dashboard?queue=needs_review">Review Desk</Link><span aria-hidden="true">/</span><span>Readiness report</span></nav>
          <p className="signal-eyebrow">Review · Evidence before decision</p>
          <h1 ref={headingRef} tabIndex={-1}>Readiness report</h1>
          {report && <p className="signal-route-header__intro">{identity?.primary} · checked against {report.rubric_title}</p>}
        </div>
        {report && <ReadinessBand status={report.status} />}
      </header>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading the readiness report…</span><i /><i /></div>
      ) : isError || !report ? (
        error instanceof ApiError && error.code === "conflict"
          ? <Alert title="This report is not ready yet" tone="info" role="alert"><ActionLink to={`/checks/${id}`} variant="secondary">View check progress</ActionLink></Alert>
          : <Alert title="Could not load this report" tone="error" role="alert"><p>{error instanceof ApiError ? error.message : "Try again."}</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>
          <section className="signal-report-hero" aria-labelledby="readiness-band-heading">
            <div className="signal-report-hero__branch" aria-hidden="true"><i /><i /><i /></div>
            <div><p className="signal-section-kicker">VERIDICAL readiness band</p><h2 id="readiness-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band is derived from the recorded criterion outcomes and integrity signals. Review the evidence below before deciding."}</p></div>
            <dl>
              <div><dt>Unresolved criteria</dt><dd>{report.pending_review_count}</dd></div>
              <div>
                <dt>Open high-severity signals</dt>
                <dd>
                  {report.unresolved_high_flag_count > 0 ? (
                    <Link
                      to="?flags_view=high#integrity-signals"
                      onClick={(event) => focusReportJumpTarget(event, { scrollIntoView: true })}
                      className="signal-report-hero__kpi-link signal-on-dark"
                      aria-label={`Open high-severity signals: ${report.unresolved_high_flag_count}. Jump to the integrity signals section, filtered to high severity.`}
                    >
                      {report.unresolved_high_flag_count}
                      <span aria-hidden="true"> →</span>
                    </Link>
                  ) : report.unresolved_high_flag_count}
                </dd>
              </div>
              <div><dt>Instructor decision</dt><dd>{report.decision ? "Recorded" : "Not recorded"}</dd></div>
            </dl>
          </section>

          <div className="signal-section-flow signal-report-orientation">
            <nav className="signal-report-jumps" aria-label="Report review order"><a href="#review-criteria" onClick={focusReportJumpTarget}><span>1</span>Resolve criteria</a><a href="#integrity-signals" onClick={focusReportJumpTarget}><span>2</span>Inspect signals</a><a href="#criteria-results-heading" onClick={focusReportJumpTarget}><span>3</span>Read criterion record</a><a href="#final-decision" onClick={focusReportJumpTarget}><span>4</span>Make decision</a></nav>

            <div className="signal-attached-flow signal-report-tools">
              <div className="signal-report-actions signal-control-cluster">
                <ActionLink to={`/report/${report.check_run_id}/document`} variant="secondary">Open manuscript</ActionLink>
                <ActionLink to={`/audit?check_run_id=${report.check_run_id}`} variant="secondary">View Audit</ActionLink>
                <Button variant="secondary" busy={exportPdf.isPending} onClick={exportReport}>{exportPdf.isPending ? "Preparing PDF" : "Export PDF"}</Button>
                <Button variant="secondary" onClick={() => setShareOpen(true)}>{shareLink ? "Manage active share link" : "Share report"}</Button>
              </div>
              <p className="signal-field-hint">Downloaded PDFs are not optimized for screen readers. Use this on-screen report for accessible review.</p>
              {exportPdf.isError && <div ref={exportErrorRef} tabIndex={-1}><Alert title="Could not export this report" tone="error" role="alert">{exportPdf.error instanceof ApiError ? exportPdf.error.message : "Try again."}</Alert></div>}
            </div>
          </div>

          {hasReportNotices && (
            <div className="signal-group-flow signal-report-notices">
              {report.previous_status && <section className="signal-report-history" aria-label="Previous check comparison"><span>Previous run</span><ReadinessBand status={report.previous_status} /><span aria-hidden="true">→</span><span>Current run</span><ReadinessBand status={report.status} /><p>Bands come from separate historical runs. Open Audit for the underlying reproducible values.</p></section>}

              {report.llm_mode !== "real" && <Alert title={report.llm_mode === "fake" ? "Test-mode AI results" : "AI mode could not be verified"} tone="warning">{report.llm_mode === "fake" ? "This report was produced with fixture responses, not real AI grading. Do not treat it as a finding about the manuscript." : "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously."}</Alert>}

              <CoverageStatement
                state="partial"
                headingId="coverage-statement-heading"
                heading="Not every check finished"
                intro="Nothing skipped here is presented as passed. Each item below names what did not finish and how to close it."
                items={coverageItems(report)}
              />
            </div>
          )}

          <SignalEscalatedPanel checkRunId={report.check_run_id} />
          <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} />
          <SignalCriteriaResults results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} />
          <SignalDecisionPanel report={report} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} />
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
