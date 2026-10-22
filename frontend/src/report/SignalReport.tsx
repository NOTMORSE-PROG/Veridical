import { WorkflowLink as Link } from "../routing/WorkflowLink";
import { SEMANTIC_SUPPORT_REVIEW } from "../domain/semanticSupport";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { type MouseEvent, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import { ApiError } from "../api/client";
import { TEST_AI_DISCLOSURE } from "../domain/aiModeDisclosure";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { CoverageStatement } from "../ui/CoverageStatement";
import { ReadinessBand } from "../ui/ReadinessBand";
import { SignalDecisionPanel } from "./SignalDecisionPanel";
import { SignalCriteriaResults, SignalEscalatedPanel, SignalFlagsPanel } from "./SignalReviewSections";
import { SignalShareDialog } from "./SignalShareDialog";
import { coverageItems } from "./reportCoverage";
import { useExportReportPdf, useReport } from "./useReport";
import { useShareLink } from "./useShare";
import { FigureReadingsSummary } from "./FigureReadings";
import { ReportReadNotice } from "./ReportReadNotice";
import { usableReport } from "./reportReadState";

// `scrollIntoView` defaults to false: the existing plain `<a href="#...">`
// jump-nav links below are same-page hash anchors the BROWSER's own
// native navigation already scrolls to before this handler runs --
// BUG-158 deliberately used `preventScroll: true` here to avoid a second,
// conflicting scroll jump on top of that native one. The hero KPI link
// (UX review finding, BUG-167 review, live-reproduced: `window.scrollY`
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
  const { data, isPending, error, refetch, fetchStatus } = useReport(id);
  const report = usableReport(data, id, error);
  const readFailed = Boolean(report && error);
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
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Readiness report</span></nav>
          <p className="signal-eyebrow">Review · Evidence before decision</p>
          <h1 ref={headingRef} tabIndex={-1}>Readiness report</h1>
          {report && <p className="signal-route-header__intro">{identity?.primary} · checked against {report.rubric_title}</p>}
        </div>
        {report && <div>{readFailed && <span>Last loaded </span>}<ReadinessBand status={report.status} /></div>}
      </header>

      {isPending && !report ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading the readiness report…</span><i /><i /></div>
      ) : !report ? (
        error instanceof ApiError && error.code === "conflict"
          ? <Alert title="This report is not ready yet" tone="info" role="alert"><ActionLink to={`/checks/${id}`} variant="secondary">View check progress</ActionLink></Alert>
          : <Alert title="Could not load this report" tone="error" role="alert"><p>{error instanceof ApiError ? error.message : "Try again."}</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>
          {readFailed && <ReportReadNotice retrying={fetchStatus === "fetching"} onRetry={() => { void refetch(); }} />}
          <section className="signal-report-hero" aria-labelledby="readiness-band-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
            <div className="signal-report-hero__branch" aria-hidden="true"><i /><i /><i /></div>
            <div><p className="signal-section-kicker">{readFailed ? "Last loaded readiness band" : "VERIDICAL readiness band"}</p><h2 id="readiness-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band is derived from the recorded criterion outcomes and integrity signals. Review the evidence below before deciding."}</p></div>
            <dl>
              <div><dt>Unresolved criteria</dt><dd>{report.pending_review_count}</dd></div>
              <div>
                <dt>Open high-severity findings counted for readiness</dt>
                <dd>
                  {report.unresolved_high_flag_count > 0 ? (
                    <Link
                      to="?flags_view=high#integrity-signals"
                      onClick={(event) => focusReportJumpTarget(event, { scrollIntoView: true })}
                      className="signal-report-hero__kpi-link signal-on-dark"
                      aria-label={`Open high-severity findings counted for readiness: ${report.unresolved_high_flag_count}. Show the High severity filter, including review leads that are not scored.`}
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

              {report.llm_mode !== "real" && <Alert title={report.llm_mode === "fake" ? TEST_AI_DISCLOSURE.title : "AI mode could not be verified"} tone="warning">{report.llm_mode === "fake" ? TEST_AI_DISCLOSURE.body : "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously."}</Alert>}

              <CoverageStatement
                state="partial"
                headingId="coverage-statement-heading"
                heading="Assessment limits"
                intro="Results below cover only the checks that ran. Unchecked content is not treated as passed."
                items={coverageItems(report)}
              />
            </div>
          )}

          {report.historical_semantic_support_unverified && <Alert title={SEMANTIC_SUPPORT_REVIEW.title} tone="warning">{SEMANTIC_SUPPORT_REVIEW.body}</Alert>}
          <SignalEscalatedPanel checkRunId={report.check_run_id} />
          <FigureReadingsSummary readings={report.figure_readings} checkRunId={report.check_run_id} />
          <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} originalityScope={report.originality_scope} />
          <SignalCriteriaResults results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} />
          <SignalDecisionPanel report={report} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} readFailed={readFailed} retrying={fetchStatus === "fetching"} onRetry={() => { void refetch(); }} />
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
