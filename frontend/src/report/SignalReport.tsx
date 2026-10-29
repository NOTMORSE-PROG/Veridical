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
import { SignalCriteriaResults, SignalFlagsPanel } from "./SignalReviewSections";
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
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";

const decidedAtFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function SignalReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const location = useLocation();
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

  const identity = report ? manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename) : undefined;
  const limits = report ? coverageItems(report) : [];
  const scopeUnconfirmed = report ? unconfirmedAutomaticRuleRows(report.results).length > 0 : false;
  const findingsCurrent = flagsQuery.isSuccess && !flagsQuery.isError && flagsQuery.fetchStatus === "idle";
  const hasOpenFindings = findingsCurrent && flagsQuery.data?.some(flag => !isOptionalComparisonLead(flag) && !flag.overridden);
  const jumpTo = (hash: string) => {
    const params = new URLSearchParams(location.search);
    params.delete("review_step");
    return { pathname: location.pathname, search: params.toString(), hash };
  };
  // The report is the record; all review work happens in manuscript review,
  // which opens on the first step that still needs the instructor.
  const workRemains = Boolean(report && (report.pending_review_count > 0 || hasOpenFindings || !findingsCurrent || !report.decision));
  const reviewPath = reviewDeskDestination(`/report/${id}/document`, location);
  const highFindingsSearch = new URLSearchParams(location.search);
  highFindingsSearch.set("flags_view", "high");
  highFindingsSearch.delete("review_step");
  const highModule = flagsQuery.data?.find(flag => !flag.overridden && flag.severity === "high" && flag.affects_readiness !== false)?.check_kind;
  if (highModule) highFindingsSearch.set("module", highModule);

  return (
    <div className="signal-route signal-page-flow signal-report">
      <header className="signal-route-header signal-report-header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Readiness report</span></nav>
          <p className="signal-eyebrow">Report · Read, export, share</p>
          <h1 ref={headingRef} id="readiness-report-heading" tabIndex={-1}>Readiness report</h1>
          {report && <p className="signal-route-header__intro"><strong>{report.subject?.basis === "confirmed_assignment_at_check" ? report.subject.title ?? report.manuscript_original_filename ?? identity?.primary : report.manuscript_original_filename ?? identity?.primary}</strong> · checked against {report.rubric_title}</p>}
          {report && report.subject?.basis !== "confirmed_assignment_at_check" && report.manuscript_group_label && <p className="signal-field-hint">Group: {report.manuscript_group_label}</p>}
          {report?.subject?.basis === "confirmed_assignment_at_check" && <p className="signal-field-hint">Group members: {report.subject.member_names.join(", ") || "Not recorded"}. Recorded when this check began.</p>}
        </div>
        {report && <div className="signal-report-header__status">{readFailed && <span>Last loaded result</span>}<ReadinessBand status={report.status} /></div>}
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
          <section className="signal-report-hero" aria-labelledby="readiness-band-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
            <div className="signal-report-hero__branch" aria-hidden="true"><i /><i /><i /></div>
            <div className="signal-report-hero__summary"><p className="signal-section-kicker">{readFailed ? "Last loaded readiness band" : scopeUnconfirmed ? "Saved readiness band" : "VERIDICAL readiness band"}</p><h2 id="readiness-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band is derived from the recorded criterion outcomes and integrity signals. Review the evidence below before deciding."}</p><RuleScopeSummary rows={report.results} checkRunId={id} />
              <div className="signal-control-cluster signal-report-task-actions">{workRemains && <ActionLink to={reviewPath} variant="brand">Continue review</ActionLink>}
              {limits.length > 0 && <Link to={jumpTo("coverage-statement-heading")} onClick={focusReportJumpTarget}>{limits.length} assessment {limits.length === 1 ? "limit" : "limits"}</Link>}
            </div>
            </div>
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
          </section>

            <div className="signal-attached-flow signal-report-tools">
              <div className="signal-report-actions signal-control-cluster">
                {!workRemains && <ActionLink to={reviewPath} variant="secondary">Open manuscript review</ActionLink>}
                <ActionLink id={`${AUDIT_RETURN.actionPrefix}${id}`} to={auditDestination(id, location)} variant="secondary" onClick={event => {
                  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                  const origin = validAuditReviewOrigin(location.pathname + location.search + location.hash, id);
                  if (origin) rememberRouteReturnFocus(origin.focusPath, auditDestination(id, location), origin.elementId, { fallbackElementId: origin.fallbackElementId, scrollBlock: origin.scrollBlock });
                }}>View Audit</ActionLink>
                <Button variant="secondary" aria-describedby="report-export-accessibility-note" aria-busy={exportPdf.isPending || undefined} aria-disabled={exportPdf.isPending || undefined} onClick={event => exportPdf.start(event.currentTarget)}>{exportPdf.isPending ? "Preparing PDF" : "Export PDF"}</Button>
                <Button variant="secondary" onClick={() => setShareOpen(true)}>{shareLink ? "Manage active share link" : "Share report"}</Button>
              </div>
              <p id="report-export-accessibility-note" className="signal-field-hint">Downloaded PDFs are not optimized for screen readers. Use this on-screen report for accessible review.</p>
              <p role="status" aria-live="polite" className="sr-only">{exportPdf.isPending ? "Preparing PDF." : exportPdf.isReady ? "PDF is ready." : ""}</p>
              {exportPdf.isError && <div ref={exportErrorRef} tabIndex={-1}><Alert title="Could not export this report" tone="error" role="alert">{exportPdf.error instanceof ApiError ? exportPdf.error.message : "Try again."}</Alert></div>}
            </div>
          <ReportAssessmentContext notices={reportAssessmentNotices(report, "report")} />
          <div className="signal-group-flow signal-report-record">
          <section id="review-criteria" tabIndex={-1} className={report.pending_review_count > 0 ? "signal-review-section signal-review-section--attention" : "signal-review-section"} aria-labelledby="report-pending-heading">
            <div className="signal-review-section__heading"><div><p className="signal-section-kicker">{report.pending_review_count > 0 ? "Waiting for you" : "Criteria"}</p><h2 id="report-pending-heading">Criteria needing your judgment</h2></div><span>{report.pending_review_count} {report.pending_review_count === 1 ? "criterion" : "criteria"}</span></div>
            <p className="signal-review-section__intro">{report.pending_review_count > 0
              ? "These have no accepted grade yet. Grade them in manuscript review, beside the manuscript."
              : "None. Every criterion has a recorded grade or assessment limit."}</p>
            {report.pending_review_count > 0 && pendingReviewQuery.data && pendingReviewQuery.data.length > 0 && <ul className="signal-report-pending-list">{pendingReviewQuery.data.map(item => <li key={item.check_result_id}>{item.criterion_text}</li>)}</ul>}
          </section>
          <FigureReadingsSummary readings={report.figure_readings} checkRunId={report.check_run_id} />
          <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} originalityScope={report.originality_scope} statisticalScope={report.statistical_check_scope} />
          <SignalCriteriaResults readOnly results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} />
          {(report.previous_status || limits.length > 0) && (
            <div className="signal-group-flow signal-report-notices">
              {report.previous_status && <section className="signal-report-history" aria-label="Previous check comparison"><span>Previous run</span><ReadinessBand status={report.previous_status} /><span aria-hidden="true">→</span><span>Current run</span><ReadinessBand status={report.status} /><p>Bands come from separate historical runs. Open Audit for the underlying reproducible values.</p></section>}
              {limits.length > 0 && <CoverageStatement
                state="partial"
                headingId="coverage-statement-heading"
                heading="Assessment limits"
                intro="Results cover only the checks that ran. Unchecked content is not treated as passed. Check these parts in the manuscript yourself before you decide."
                items={limits}
              />}
            </div>
          )}
          <section id="final-decision" tabIndex={-1} className="signal-review-section" aria-labelledby="decision-heading">
            <div className="signal-review-section__heading"><div><p className="signal-section-kicker">Instructor authority</p><h2 id="decision-heading" tabIndex={-1}>Instructor decision</h2></div>{report.decision && <span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span>}</div>
            {report.decision ? <>
              <p>Recorded by you{report.decided_at ? ` on ${decidedAtFormatter.format(new Date(report.decided_at))}` : ""}. This is the instructor's decision, not an automated approval or block.</p>
              {report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}
            </> : <p>Not recorded yet. You record the decision in manuscript review, after checking the evidence.</p>}
          </section>
          </div>
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
