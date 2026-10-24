// Screen 4l — public, read-only adviser view (F8.7). This is deliberately
// outside the authenticated workspace and exposes only the public contract.
import { type MouseEvent, type ReactNode, useEffect, useRef } from "react";
import { Link, useLocation, useParams } from "react-router";
import { ApiError } from "../api/client";
import { DECISION_LABEL } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { isUnresolvedSharedCriterion } from "../domain/sharedCriterionReview";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ActionLink } from "../ui/ActionLink";
import { SignalMark, SignalWordmark } from "../ui/SignalMark";
import { SignalCriteriaResults, SignalPublicFlags } from "./SignalReviewSections";
import { useSharedReport } from "./useShare";
import { RuleScopeSummary } from "./RuleScopeNotice";
import { unconfirmedAutomaticRuleRows } from "../domain/ruleScope";
import { SharedAssessmentContext } from "./SharedAssessmentContext";

function focusSharedSection(event: MouseEvent<HTMLAnchorElement>) {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const targetId = event.currentTarget.hash.slice(1);
  requestAnimationFrame(() => {
    const target = document.getElementById(targetId);
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
  });
}

function useNoindexMeta() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => {
      document.head.removeChild(meta);
    };
  }, []);
}

function SharedChrome({ children, reportAvailable = true }: { children: ReactNode; reportAvailable?: boolean }) {
  return (
    <div className="signal-theme signal-shared-shell">
      <a href="#main-content" className="signal-skip-link">Skip to main content</a>
      <header className="signal-shared-topbar"><Link to="/" className="signal-brand-link signal-on-dark" aria-label="VERIDICAL home"><SignalMark inverse /><SignalWordmark inverse /></Link><span>Faculty shared view</span></header>
      {reportAvailable && <p className="signal-shared-readonly"><strong>Read-only shared report.</strong> No account is required, and nothing on this page can be edited.</p>}
      <main id="main-content" tabIndex={-1} className="signal-shared-main">{children}</main>
      <footer className="signal-shared-footer"><p>VERIDICAL is a BSIT capstone project at the Technological Institute of the Philippines, Manila. It is not an official T.I.P. system.</p><p>This view always shows the report's current state, not a snapshot from the day it was shared. The link is not indexed by search engines; treat it as private to its recipients.</p></footer>
    </div>
  );
}

export function AdviserViewPage() {
  const { token } = useParams<{ token: string }>();
  const location = useLocation();
  const { data, isPending, isError, error } = useSharedReport(token ?? "");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLHeadingElement>(null);
  useNoindexMeta();

  useEffect(() => {
    document.title = "Readiness report (shared) - VERIDICAL";
  }, []);
  useEffect(() => {
    if (isError) errorRef.current?.focus();
  }, [isError]);

  if (isPending) return <SharedChrome><div className="signal-shared-route signal-page-flow"><header className="signal-route-header"><div><p className="signal-eyebrow">Shared faculty review</p><h1 ref={headingRef} tabIndex={-1}>Readiness report</h1></div></header><div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading shared report…</span><i /><i /></div></div></SharedChrome>;

  if (isError || !data) {
    const notFound = error instanceof ApiError && error.code === "not_found";
    const gone = error instanceof ApiError && error.code === "gone";
    const title = notFound ? "Link not found" : gone ? "Link no longer available" : "Could not load this report";
    const message = error instanceof ApiError && (notFound || gone) ? error.message : "This shared report could not be loaded right now.";
    return <SharedChrome reportAvailable={false}><div className="signal-shared-route signal-page-flow signal-shared-error"><header className="signal-route-header"><div><p className="signal-eyebrow">Shared faculty review</p><h1 ref={errorRef} tabIndex={-1}>{title}</h1></div></header><Alert title={message} tone="error" role="alert">{notFound || gone ? "Contact the instructor who sent this link if you believe it should still be available." : <Button variant="secondary" onClick={() => window.location.reload()}>Reload</Button>}</Alert></div></SharedChrome>;
  }

  const { report, flags } = data;
  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  const pendingCount = report.results.filter(isUnresolvedSharedCriterion).length;
  const scopeUnconfirmed = unconfirmedAutomaticRuleRows(report.results, true).length > 0;

  return (
    <SharedChrome>
      <div className="signal-shared-route signal-page-flow">
        <header className="signal-route-header signal-shared-report-header"><div><p className="signal-eyebrow">Shared faculty review</p><h1 ref={headingRef} tabIndex={-1}>Readiness report</h1><p className="signal-route-header__intro">{identity.primary} · checked against {report.rubric_title}</p></div></header>

        <SharedAssessmentContext report={report} />

        <section className="signal-shared-verdict" aria-labelledby="shared-band-heading"><div><p className="signal-section-kicker">{scopeUnconfirmed ? "Saved readiness band" : "VERIDICAL readiness band"}</p><h2 id="shared-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity signals. It does not approve or block a defense."}</p><RuleScopeSummary rows={report.results} checkRunId={report.check_run_id} publicView linkToRecords={false} /></div><dl><div><dt>Unresolved criteria</dt><dd>{pendingCount}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div></dl><div className="signal-control-cluster"><ActionLink to={{ pathname: location.pathname, search: location.search, hash: "criteria-results-heading" }} onClick={focusSharedSection} variant="secondary" className="signal-on-dark">Review criterion records</ActionLink><ActionLink to={{ pathname: location.pathname, search: location.search, hash: "shared-decision-heading" }} onClick={focusSharedSection} variant="quiet" className="signal-on-dark">Read final decision</ActionLink></div></section>

        <SignalCriteriaResults results={report.results} checkRunId={report.check_run_id} publicView />
        <SignalPublicFlags flags={flags} originalityScope={report.originality_scope} checkRunId={report.check_run_id} />

        <section className="signal-shared-decision" aria-labelledby="shared-decision-heading"><div><p className="signal-section-kicker">Instructor authority</p><h2 id="shared-decision-heading" tabIndex={-1}>Final decision</h2></div>{report.decision ? <><span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span><p>Recorded by the instructor. VERIDICAL did not make this decision.</p>{report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}</> : <><p>No final instructor decision has been recorded.</p><p>The readiness band above is an aid for review, not an approval or block.</p></>}</section>
      </div>
    </SharedChrome>
  );
}
