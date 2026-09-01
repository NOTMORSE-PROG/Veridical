// Screen 4l — public, read-only adviser view (F8.7). This is deliberately
// outside the authenticated workspace and exposes only the public contract.
import { type ReactNode, useEffect, useRef } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { DECISION_LABEL } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ReadinessBand } from "../ui/ReadinessBand";
import { SignalMark, SignalWordmark } from "../ui/SignalMark";
import { SignalCriteriaResults, SignalPublicFlags } from "./SignalReviewSections";
import { useSharedReport } from "./useShare";

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
  const pendingCount = report.results.filter((row) => row.outcome === "escalated" || row.outcome === "api_down" || row.outcome === "quota_exhausted").length;

  return (
    <SharedChrome>
      <div className="signal-shared-route signal-page-flow">
        <header className="signal-route-header signal-shared-report-header"><div><p className="signal-eyebrow">Shared faculty review</p><h1 ref={headingRef} tabIndex={-1}>Readiness report</h1><p className="signal-route-header__intro">{identity.primary} · checked against {report.rubric_title}</p></div><ReadinessBand status={report.status} /></header>

        <section className="signal-shared-verdict" aria-labelledby="shared-band-heading"><div><p className="signal-section-kicker">VERIDICAL readiness band</p><h2 id="shared-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity signals. It does not approve or block a defense."}</p></div><dl><div><dt>Items still awaiting instructor review</dt><dd>{pendingCount}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div></dl></section>

        {report.llm_mode !== "real" && <Alert title={report.llm_mode === "fake" ? "Test-mode AI results" : "AI mode could not be verified"} tone="warning">{report.llm_mode === "fake" ? "Fixture responses produced this report. Do not treat AI-derived content as findings about the manuscript." : "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously."}</Alert>}
        {report.rubric_needs_review && <Alert title="The required format had parser uncertainty" tone="warning"><p>The instructor activated it despite the warning.</p>{report.rubric_parse_issues?.length ? <ul>{report.rubric_parse_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}</Alert>}

        <SignalPublicFlags flags={flags} />
        <SignalCriteriaResults results={report.results} checkRunId={report.check_run_id} publicView />

        <section className="signal-shared-decision" aria-labelledby="shared-decision-heading"><div><p className="signal-section-kicker">Instructor authority</p><h2 id="shared-decision-heading">Final decision</h2></div>{report.decision ? <><span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span><p>Recorded by the instructor. VERIDICAL did not make this decision.</p>{report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}</> : <Alert title="No final instructor decision has been recorded" tone="info">The readiness band above is an aid for review, not an approval or block.</Alert>}</section>
      </div>
    </SharedChrome>
  );
}
