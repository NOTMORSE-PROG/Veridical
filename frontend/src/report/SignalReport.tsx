import { pendingCriterionSourceAction } from "./criterionSourceReturn";
import { WorkflowLink as Link } from "../routing/WorkflowLink";
import { reportAssessmentNotices } from "../domain/reportAssessmentContext";
import { ReportAssessmentContext } from "./ReportAssessmentContext";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { type MouseEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router";
import { ApiError } from "../api/client";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { DECISION_LABEL } from "../domain/decisionTone";
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
import { useEscalatedItems, useFlags, useReport } from "./useReport";
import { useShareLink } from "./useShare";
import { FigureReadingsSummary } from "./FigureReadings";
import { ReportReadNotice } from "./ReportReadNotice";
import { usableReport } from "./reportReadState";
import { useReportPdfExportAction } from "./useReportPdfExportAction";
import { REPORT_SECTION_TARGETS } from "./reportSectionNavigation";
import { REPORT_STEPS, reportStep } from "./reviewStep";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { SavedJudgmentAccess } from "./SavedJudgmentAccess";
import { selectedCriterionId, SELECTED_CRITERION_PARAM } from "./criterionReviewNavigation";

export function SignalReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const location = useLocation();
  const step = reportStep(location.search, location.hash);
  const stepIndex = REPORT_STEPS.findIndex(item => item.id === step);
  const pendingJump = useRef<{ destination: string; target: string; scroll: boolean; focus: FocusContinuation } | undefined>(undefined);
  useEffect(() => () => pendingJump.current?.focus.dispose(), []);
  useLayoutEffect(() => {
    const intent = pendingJump.current;
    if (!intent || intent.destination !== location.pathname + location.search + location.hash) return;
    const target = document.getElementById(intent.target);
    if (!target || target.closest("[hidden]")) return;
    pendingJump.current = undefined;
    if (intent.focus.focus(target, { preventScroll: true }) && intent.scroll) target.scrollIntoView({ block: "start" });
  });
  function focusReportJumpTarget(event: MouseEvent<HTMLAnchorElement>, { scrollIntoView = true }: { scrollIntoView?: boolean } = {}) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.currentTarget;
    pendingJump.current?.focus.dispose();
    pendingJump.current = { destination: link.pathname + link.search + link.hash,
      target: decodeURIComponent(link.hash.slice(1)), scroll: scrollIntoView,
      focus: claimFocusContinuation(link) };
  }

  const { data, isPending, error, refetch, fetchStatus } = useReport(id);
  const report = usableReport(data, id, error);
  const readFailed = Boolean(report && error);
  const { data: shareLink } = useShareLink(id);
  const [shareOpen, setShareOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const exportErrorRef = useRef<HTMLDivElement>(null);
  const exportPdf = useReportPdfExportAction(id, exportErrorRef);
  const flagsQuery = useFlags(id);
  const pendingReviewQuery = useEscalatedItems(id);
  // Capture before route-focus consumes its registration on this navigation.
  const sourceReturnNavigation = useMemo(() => ({ key: location.key, action: pendingCriterionSourceAction(
    pendingRouteReturnElement(location.pathname + location.search), id) }), [id, location.pathname, location.search, location.key]);
  const pendingSourceReturn = sourceReturnNavigation.action;
  const pendingSourceSettled = Boolean(report && !error && fetchStatus === "idle"
    && pendingReviewQuery.isSuccess && !pendingReviewQuery.isError && pendingReviewQuery.fetchStatus === "idle");
  const [missingSourceAction, setMissingSourceAction] = useState(false);
  useEffect(() => {
    setMissingSourceAction(Boolean(pendingSourceReturn && pendingSourceSettled && !document.getElementById(pendingSourceReturn.elementId)));
  }, [pendingSourceReturn, pendingSourceSettled, pendingReviewQuery.data, report]);
  const auditOrigin = validAuditReviewOrigin(location.pathname + location.search + location.hash, id);
  const returningToAuditSection = Boolean(location.hash && auditOrigin
    && pendingRouteReturnElement(location.pathname + location.search) === auditOrigin.elementId);
  const sectionFocus = pendingRouteReturnElement(location.pathname + location.search);
  const returningToReportSection = REPORT_SECTION_TARGETS.some(target => target === sectionFocus);
  useRouteFocus("Readiness report - VERIDICAL", headingRef, undefined, pendingSourceReturn
    ? { prefix: pendingSourceReturn.elementId, pending: !pendingSourceSettled }
    : returningToAuditSection
    ? { prefix: auditOrigin!.elementId, pending: isPending || fetchStatus !== "idle" }
    : returningToReportSection ? { prefix: sectionFocus!, pending: isPending || fetchStatus !== "idle" }
    : { prefix: "comparison-lead-", pending: isPending || flagsQuery.isPending || flagsQuery.fetchStatus !== "idle" });

  const criterionEntry = useRef<{ key: string; claim?: FocusContinuation } | undefined>(undefined);
  useLayoutEffect(() => {
    if (criterionEntry.current?.key !== location.key) {
      criterionEntry.current?.claim?.dispose();
      const requested = selectedCriterionId(new URLSearchParams(location.search).get(SELECTED_CRITERION_PARAM));
      criterionEntry.current = { key: location.key, claim: step === "criteria" && requested !== null
        && !pendingSourceReturn && !returningToAuditSection && !returningToReportSection
        ? claimFocusContinuation() : undefined };
    }
    const intent = criterionEntry.current;
    if (!intent?.claim || !pendingSourceSettled) return;
    const requested = selectedCriterionId(new URLSearchParams(location.search).get(SELECTED_CRITERION_PARAM));
    const item = pendingReviewQuery.data?.find(row => row.criterion_id === requested);
    const target = item ? document.getElementById(`signal-resolution-heading-${item.check_result_id}`) : null;
    if (target && !target.closest("[hidden]") && intent.claim.focus(target, { preventScroll: true })) {
      document.querySelector(".signal-criterion-navigation")?.scrollIntoView({ block: "start" });
    }
    intent.claim.dispose();
    intent.claim = undefined;
  });
  useEffect(() => () => {
    criterionEntry.current?.claim?.dispose();
    criterionEntry.current = undefined;
  }, []);

  const identity = report ? manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename) : undefined;
  const limits = report ? coverageItems(report) : [];
  const scopeUnconfirmed = report ? unconfirmedAutomaticRuleRows(report.results).length > 0 : false;
  const findingsCurrent = flagsQuery.isSuccess && !flagsQuery.isError && flagsQuery.fetchStatus === "idle";
  const hasOpenFindings = findingsCurrent && flagsQuery.data?.some(flag => !isOptionalComparisonLead(flag) && !flag.overridden);
  const taskTarget = report?.pending_review_count ? "review-criteria" : hasOpenFindings || !findingsCurrent ? "integrity-signals" : "criteria-results-heading";
  const taskLabel = report?.pending_review_count ? `Review ${report.pending_review_count} ${report.pending_review_count === 1 ? "criterion" : "criteria"}`
    : !findingsCurrent ? "Review evidence" : hasOpenFindings ? "Review findings" : "Review recorded results";
  const jumpTo = (hash: string) => {
    const params = new URLSearchParams(location.search);
    const next = REPORT_STEPS.find(item => item.target === hash);
    if (next) params.set("review_step", next.id);
    return { pathname: location.pathname, search: params.toString(), hash };
  };
  const highFindingsSearch = new URLSearchParams(location.search);
  highFindingsSearch.set("flags_view", "high");
  highFindingsSearch.set("review_step", "findings");
  const highModule = flagsQuery.data?.find(flag => !flag.overridden && flag.severity === "high" && flag.affects_readiness !== false)?.check_kind;
  if (highModule) highFindingsSearch.set("module", highModule);

  return (
    <div className="signal-route signal-page-flow signal-report">
      <header className="signal-route-header signal-report-header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Readiness report</span></nav>
          <p className="signal-section-kicker">Readiness report</p>
          <h1 ref={headingRef} id="readiness-report-heading" tabIndex={-1}>{report?.subject?.basis === "confirmed_assignment_at_check" ? report.subject.title ?? report.manuscript_original_filename ?? identity?.primary ?? "Readiness report" : report?.manuscript_original_filename ?? identity?.primary ?? "Readiness report"}</h1>
          {report && <p className="signal-route-header__intro">Required format: {report.rubric_title}</p>}
          {report && report.subject?.basis !== "confirmed_assignment_at_check" && report.manuscript_group_label && <p className="signal-field-hint">Group: {report.manuscript_group_label}</p>}
          {report?.subject?.basis === "confirmed_assignment_at_check" && <div><p>Group members: {report.subject.member_names.join(", ") || "Not recorded"}</p><p className="signal-field-hint">Recorded when this check began.</p></div>}
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
          <p role="status" aria-live="polite" className={missingSourceAction ? undefined : "sr-only"}>{missingSourceAction ? "The original source action is no longer available. Review the current report below." : ""}</p>
          <ReportAssessmentContext notices={reportAssessmentNotices(report, "report")} />
          <section className="signal-report-hero" aria-labelledby="readiness-band-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
            <div className="signal-report-hero__summary"><p className="signal-section-kicker">{readFailed ? "Last loaded readiness band" : scopeUnconfirmed ? "Saved readiness band" : "VERIDICAL readiness band"}</p><h2 id="readiness-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band is derived from the recorded criterion outcomes and integrity signals. Review the evidence below before deciding."}</p><RuleScopeSummary rows={report.results} checkRunId={id} /></div>
            <dl>
              <div><dt>Unresolved criteria</dt><dd>{report.pending_review_count}</dd></div>
              <div>
                <dt>Open high-severity findings counted for readiness</dt>
                <dd>
                  {report.unresolved_high_flag_count > 0 ? (
                    <Link
                      to={{ pathname: location.pathname, search: highFindingsSearch.toString(), hash: "integrity-signals" }}
                      onClick={(event) => focusReportJumpTarget(event, { scrollIntoView: true })}
                      className="signal-report-hero__kpi-link"
                      aria-label={`Open high-severity findings counted for readiness: ${report.unresolved_high_flag_count}. Show the High severity filter, including review leads that are not scored.`}
                    >
                      {report.unresolved_high_flag_count}
                      <span aria-hidden="true"> →</span>
                    </Link>
                  ) : report.unresolved_high_flag_count}
                </dd>
              </div>
              <div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div>
            </dl>
            <div className="signal-control-cluster signal-report-task-actions"><Link to={jumpTo(taskTarget)} onClick={focusReportJumpTarget}>{taskLabel}</Link>
              {limits.length > 0 && <Link to={jumpTo("coverage-statement-heading")} onClick={focusReportJumpTarget}>{limits.length} assessment {limits.length === 1 ? "limit" : "limits"}</Link>}
            </div>
          </section>

          <div className="signal-section-flow signal-report-orientation">
            <nav className="signal-report-jumps" aria-label="Report review order">{REPORT_STEPS.map((item, index) => <Link key={item.id} to={jumpTo(item.target)} aria-current={step === item.id ? "step" : undefined} onClick={focusReportJumpTarget}><span>{index + 1}</span>{item.label}</Link>)}</nav>
            <p className="signal-field-hint" role="status">Step {stepIndex + 1} of {REPORT_STEPS.length}: {REPORT_STEPS[stepIndex].label}. Selecting a step does not save a grade or decision.</p>

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

          <SavedJudgmentAccess checkRunId={report.check_run_id} hideEmpty />
          <div hidden={step !== "criteria"}><SignalEscalatedPanel checkRunId={report.check_run_id} assessmentContextExternal savedAccessExternal /></div>
          <div hidden={step !== "findings"}>
            <FigureReadingsSummary readings={report.figure_readings} checkRunId={report.check_run_id} />
            <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} originalityScope={report.originality_scope} statisticalScope={report.statistical_check_scope} />
          </div>
          <div hidden={step !== "grades"}><SignalCriteriaResults results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} /></div>
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

          <div hidden={step !== "decision"}><SignalDecisionPanel report={report} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} readFailed={readFailed} retrying={fetchStatus === "fetching"} onRetry={() => { void refetch(); }} /></div>
          <nav className="signal-control-cluster" aria-label="Continue report review">
            {stepIndex > 0 && <Link to={jumpTo(REPORT_STEPS[stepIndex - 1].target)} onClick={focusReportJumpTarget}>Back: {REPORT_STEPS[stepIndex - 1].label}</Link>}
            {stepIndex < REPORT_STEPS.length - 1 && <Link to={jumpTo(REPORT_STEPS[stepIndex + 1].target)} onClick={focusReportJumpTarget}>Continue: {REPORT_STEPS[stepIndex + 1].label}</Link>}
          </nav>
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
