import { type MouseEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { IntegrityCheckStatusOut } from "../api/types";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
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

function IntegrityDisclosure({ status }: { status: IntegrityCheckStatusOut }) {
  const unavailable = status.n_skipped_api_down;
  const capacity = status.n_skipped_quota;
  const parse = status.n_skipped_parse_failure;
  return (
    <Alert title={`${INTEGRITY_LABEL[status.check_kind]} was not fully assessed`} tone="warning">
      <p>The check recorded {status.outcome === "api_down" ? "a service interruption" : status.outcome === "quota_exhausted" ? "a free-capacity limit" : "an unverifiable result"}. Nothing skipped is presented as passed.</p>
      <ul>{unavailable > 0 && <li>{unavailable} item{unavailable === 1 ? "" : "s"} skipped because a service was unavailable.</li>}{capacity > 0 && <li>{capacity} item{capacity === 1 ? "" : "s"} skipped because daily AI capacity was spent.</li>}{parse > 0 && <li>{parse} item{parse === 1 ? "" : "s"} skipped because the source could not be parsed reliably.</li>}</ul>
    </Alert>
  );
}

function focusReportJumpTarget(event: MouseEvent<HTMLAnchorElement>) {
  const targetId = decodeURIComponent(event.currentTarget.hash.slice(1));
  requestAnimationFrame(() => {
    document.getElementById(targetId)?.focus({ preventScroll: true });
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

  return (
    <div className="signal-route signal-report">
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
            <dl><div><dt>Unresolved criteria</dt><dd>{report.pending_review_count}</dd></div><div><dt>Open high-severity signals</dt><dd>{report.unresolved_high_flag_count}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? "Recorded" : "Not recorded"}</dd></div></dl>
          </section>

          <nav className="signal-report-jumps" aria-label="Report review order"><a href="#review-criteria" onClick={focusReportJumpTarget}><span>1</span>Resolve criteria</a><a href="#integrity-signals" onClick={focusReportJumpTarget}><span>2</span>Inspect signals</a><a href="#criteria-results-heading" onClick={focusReportJumpTarget}><span>3</span>Read criterion record</a><a href="#final-decision" onClick={focusReportJumpTarget}><span>4</span>Make decision</a></nav>

          <div className="signal-report-actions">
            <ActionLink to={`/report/${report.check_run_id}/document`} variant="secondary">Open manuscript</ActionLink>
            <ActionLink to={`/audit?check_run_id=${report.check_run_id}`} variant="secondary">View Audit</ActionLink>
            <Button variant="secondary" busy={exportPdf.isPending} onClick={exportReport}>{exportPdf.isPending ? "Preparing PDF" : "Export PDF"}</Button>
            <Button variant="secondary" onClick={() => setShareOpen(true)}>{shareLink ? "Manage active share link" : "Share report"}</Button>
          </div>
          <p className="signal-field-hint">Downloaded PDFs are not optimized for screen readers. Use this on-screen report for accessible review.</p>
          {exportPdf.isError && <div ref={exportErrorRef} tabIndex={-1}><Alert title="Could not export this report" tone="error" role="alert">{exportPdf.error instanceof ApiError ? exportPdf.error.message : "Try again."}</Alert></div>}

          {report.previous_status && <section className="signal-report-history" aria-label="Previous check comparison"><span>Previous run</span><ReadinessBand status={report.previous_status} /><span aria-hidden="true">→</span><span>Current run</span><ReadinessBand status={report.status} /><p>Bands come from separate historical runs. Open Audit for the underlying reproducible values.</p></section>}

          {report.llm_mode !== "real" && <Alert title={report.llm_mode === "fake" ? "Test-mode AI results" : "AI mode could not be verified"} tone="warning">{report.llm_mode === "fake" ? "This report was produced with fixture responses, not real AI grading. Do not treat it as a finding about the manuscript." : "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously."}</Alert>}

          {report.rubric_needs_review && <Alert title="The required format had unresolved parser uncertainty" tone="warning"><p>The instructor activated it despite the parser warning. Check the criterion record against the original format.</p>{report.rubric_parse_issues?.length ? <ul>{report.rubric_parse_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}</Alert>}

          {(report.integrity_check_status ?? []).map((status) => <IntegrityDisclosure key={status.check_kind} status={status} />)}

          <SignalEscalatedPanel checkRunId={report.check_run_id} />
          <SignalFlagsPanel checkRunId={report.check_run_id} />
          <SignalCriteriaResults results={report.results.filter((row) => row.outcome !== "escalated")} checkRunId={report.check_run_id} />
          <SignalDecisionPanel report={report} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} />
        </>
      )}

      {shareOpen && report && <SignalShareDialog checkRunId={report.check_run_id} manuscriptLabel={identity?.primary ?? report.manuscript_group_label} onClose={() => setShareOpen(false)} />}
    </div>
  );
}
