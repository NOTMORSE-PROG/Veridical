import { WorkflowLink as Link } from "../routing/WorkflowLink";
import { reportAssessmentNotices } from "../domain/reportAssessmentContext";
import { ReportAssessmentContext } from "./ReportAssessmentContext";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { type MouseEvent, useRef, useState } from "react";
import { useLocation, useParams } from "react-router";
import { ApiError } from "../api/client";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { pendingRouteReturnElement, rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { auditDestination, validAuditReviewOrigin } from "../routing/auditReturn";
import { AUDIT_RETURN } from "../config/signalRoutes";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { CoverageStatement } from "../ui/CoverageStatement";
import { ReadinessBand } from "../ui/ReadinessBand";
import { SignalDecisionPanel } from "./SignalDecisionPanel";
import { SignalCriteriaResults, SignalEscalatedPanel, SignalFlagsPanel } from "./SignalReviewSections";
import { SignalShareDialog } from "./SignalShareDialog";
import { coverageItems } from "./reportCoverage";
import { unconfirmedAutomaticRuleRows } from "../domain/ruleScope";
import { RuleScopeSummary } from "./RuleScopeNotice";
import { isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { useFlags, useReport } from "./useReport";
import { useShareLink } from "./useShare";
import { FigureReadingsSummary } from "./FigureReadings";
import { ReportReadNotice } from "./ReportReadNotice";
import { usableReport } from "./reportReadState";
import { useReportPdfExportAction } from "./useReportPdfExportAction";
import { REPORT_SECTION_TARGETS } from "./reportSectionNavigation";

// Router-managed jumps preserve draft blockers and route state. They need
// explicit target scrolling and focus after navigation; modified clicks
// retain the browser's ordinary new-tab behavior.
function focusReportJumpTarget(
  event: MouseEvent<HTMLAnchorElement>,
  { scrollIntoView = true }: { scrollIntoView?: boolean } = {},
) {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
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
  const location = useLocation();
  const { data, isPending, error, refetch, fetchStatus } = useReport(id);
  const report = usableReport(data, id, error);
  const readFailed = Boolean(report && error);
  const { data: shareLink } = useShareLink(id);
  const [shareOpen, setShareOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const exportErrorRef = useRef<HTMLDivElement>(null);
  const exportPdf = useReportPdfExportAction(id, exportErrorRef);
  const flagsQuery = useFlags(id);
  const auditOrigin = validAuditReviewOrigin(location.pathname + location.search + location.hash, id);
  const returningToAuditSection = Boolean(location.hash && auditOrigin
    && pendingRouteReturnElement(location.pathname + location.search) === auditOrigin.elementId);
  const sectionFocus = pendingRouteReturnElement(location.pathname + location.search);
  const returningToReportSection = REPORT_SECTION_TARGETS.some(target => target === sectionFocus);
  useRouteFocus("Readiness report - VERIDICAL", headingRef, undefined, returningToAuditSection
    ? { prefix: auditOrigin!.elementId, pending: isPending || fetchStatus !== "idle" }
    : returningToReportSection ? { prefix: sectionFocus!, pending: isPending || fetchStatus !== "idle" }
    : { prefix: "comparison-lead-", pending: isPending || flagsQuery.isPending || flagsQuery.fetchStatus !== "idle" });

  const identity = report ? manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename) : undefined;
  const limits = report ? coverageItems(report) : [];
  const scopeUnconfirmed = report ? unconfirmedAutomaticRuleRows(report.results).length > 0 : false;
  const findingsCurrent = flagsQuery.isSuccess && !flagsQuery.isError && flagsQuery.fetchStatus === "idle";
  const hasOpenFindings = findingsCurrent && flagsQuery.data?.some(flag => !isOptionalComparisonLead(flag) && !flag.overridden);
  const taskTarget = report?.pending_review_count ? "review-criteria" : hasOpenFindings || !findingsCurrent ? "integrity-signals" : "criteria-results-heading";
  const taskLabel = report?.pending_review_count ? `Review ${report.pending_review_count} ${report.pending_review_count === 1 ? "criterion" : "criteria"}`
    : !findingsCurrent ? "Review evidence" : hasOpenFindings ? "Review findings" : "Review recorded results";
  const jumpTo = (hash: string) => ({ pathname: location.pathname, search: location.search, hash });
  const highFindingsSearch = new URLSearchParams(location.search);
  highFindingsSearch.set("flags_view", "high");

  return (
    <div className="signal-route signal-page-flow signal-report">
      <header className="signal-route-header signal-report-header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Readiness report</span></nav>
          <h1 ref={headingRef} id="readiness-report-heading" tabIndex={-1}>Readiness report</h1>
          {report && <p className="signal-route-header__intro">{identity?.primary} · checked against {report.rubric_title}</p>}
        </div>
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
          <ReportAssessmentContext notices={reportAssessmentNotices(report, "report")} />
          <section className="signal-report-hero" aria-labelledby="readiness-band-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
            <div className="signal-report-hero__branch" aria-hidden="true"><i /><i /><i /></div>
            <div><p className="signal-section-kicker">{readFailed ? "Last loaded readiness band" : scopeUnconfirmed ? "Saved readiness band" : "VERIDICAL readiness band"}</p><h2 id="readiness-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band is derived from the recorded criterion outcomes and integrity signals. Review the evidence below before deciding."}</p><RuleScopeSummary rows={report.results} checkRunId={id} /></div>
            <dl>
              <div><dt>Unresolved criteria</dt><dd>{report.pending_review_count}</dd></div>
              <div>
                <dt>Open high-severity findings counted for readiness</dt>
                <dd>
                  {report.unresolved_high_flag_count > 0 ? (
                    <Link
                      to={{ pathname: location.pathname, search: highFindingsSearch.toString(), hash: "integrity-signals" }}
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
            <div className="signal-control-cluster signal-report-task-actions"><Link className="signal-on-dark" to={jumpTo(taskTarget)} onClick={focusReportJumpTarget}>{taskLabel}</Link>
              {limits.length > 0 && <Link className="signal-on-dark" to={jumpTo("coverage-statement-heading")} onClick={focusReportJumpTarget}>{limits.length} assessment {limits.length === 1 ? "limit" : "limits"}</Link>}
            </div>
          </section>

          <div className="signal-section-flow signal-report-orientation">
            <nav className="signal-report-jumps" aria-label="Report review order"><Link to={jumpTo("review-criteria")} onClick={focusReportJumpTarget}><span>1</span>Resolve criteria</Link><Link to={jumpTo("integrity-signals")} onClick={focusReportJumpTarget}><span>2</span>Inspect signals</Link><Link to={jumpTo("criteria-results-heading")} onClick={focusReportJumpTarget}><span>3</span>Read criterion record</Link><Link to={jumpTo("final-decision")} onClick={focusReportJumpTarget}><span>4</span>Make decision</Link></nav>

            <div className="signal-attached-flow signal-report-tools">
              <div className="signal-report-actions signal-control-cluster">
                <ActionLink to={`/report/${report.check_run_id}/document`} variant="quiet">Open manuscript</ActionLink>
                <ActionLink id={`${AUDIT_RETURN.actionPrefix}${id}`} to={auditDestination(id, location)} variant="quiet" onClick={event => {
                  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  const origin = validAuditReviewOrigin(location.pathname + location.search + location.hash, id);
                  if (origin) rememberRouteReturnFocus(origin.focusPath, auditDestination(id, location), origin.elementId, { fallbackElementId: origin.fallbackElementId, scrollBlock: origin.scrollBlock });
                }}>View Audit</ActionLink>
                <Button variant="quiet" aria-describedby="report-export-accessibility-note" aria-busy={exportPdf.isPending || undefined} aria-disabled={exportPdf.isPending || undefined} onClick={event => exportPdf.start(event.currentTarget)}>{exportPdf.isPending ? "Preparing PDF" : "Export PDF"}</Button>
                <Button variant="quiet" onClick={() => setShareOpen(true)}>{shareLink ? "Manage active share link" : "Share report"}</Button>
              </div>
              <p id="report-export-accessibility-note" className="signal-field-hint">Downloaded PDFs are not optimized for screen readers. Use this on-screen report for accessible review.</p>
              <p role="status" aria-live="polite" className="sr-only">{exportPdf.isPending ? "Preparing PDF." : exportPdf.isReady ? "PDF is ready." : ""}</p>
              {exportPdf.isError && <div ref={exportErrorRef} tabIndex={-1}><Alert title="Could not export this report" tone="error" role="alert">{exportPdf.error instanceof ApiError ? exportPdf.error.message : "Try again."}</Alert></div>}
            </div>
          </div>

          <SignalEscalatedPanel checkRunId={report.check_run_id} assessmentContextExternal />
          <FigureReadingsSummary readings={report.figure_readings} checkRunId={report.check_run_id} />
          <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} originalityScope={report.originality_scope} />
          <SignalCriteriaResults results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} />
          {(report.previous_status || limits.length > 0) && (
            <div className="signal-group-flow signal-report-notices">
              {report.previous_status && <section className="signal-report-history" aria-label="Previous check comparison"><span>Previous run</span><ReadinessBand status={report.previous_status} /><span aria-hidden="true">→</span><span>Current run</span><ReadinessBand status={report.status} /><p>Bands come from separate historical runs. Open Audit for the underlying reproducible values.</p></section>}


              <CoverageStatement
                state="partial"
                headingId="coverage-statement-heading"
                heading="Assessment limits"
                intro="Results cover only the checks that ran. Unchecked content is not treated as passed."
                items={limits}
              />
            </div>
          )}

          <SignalDecisionPanel report={report} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} readFailed={readFailed} retrying={fetchStatus === "fetching"} onRetry={() => { void refetch(); }} />
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
