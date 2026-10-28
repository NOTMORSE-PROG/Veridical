// Screen 4l — public, read-only adviser view (F8.7). This is deliberately
// outside the authenticated workspace and exposes only the public contract.
import { type MouseEvent, type ReactNode, useEffect, useLayoutEffect, useRef } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router";
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
import { SharedEvidencePacket, useSharedEvidenceManifest } from "./SharedEvidencePacket";
import { IntegrityModuleNavigation, selectedIntegrityModule } from "./IntegrityModules";
import { CitationSourceLinks } from "../flags/CitationSourceLinks";
import { CitationComparison } from "../flags/CitationComparison";
import { selectedCriterionId } from "./criterionReviewNavigation";
import { criterionRecordId } from "./savedJudgment";
import { resultDisplay } from "./ResultsTable";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";

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
      <footer className="signal-shared-footer"><p>VERIDICAL is a BSIT capstone project at the Technological Institute of the Philippines, Manila. It is not an official T.I.P. system.</p><p>The assessment shows the report's current state. Additional shared detail is shown only while its saved assessment revision matches. The link is not indexed by search engines; treat it as private to its recipients.</p></footer>
    </div>
  );
}

export function AdviserViewPage() {
  const { token } = useParams<{ token: string }>();
  const location = useLocation();
  const { data, isPending, isError, error } = useSharedReport(token ?? "");
  const evidence = useSharedEvidenceManifest(token ?? "", Boolean(data) && !isError);
  const [params, setParams] = useSearchParams();
  const selectedModule = selectedIntegrityModule(params.get("module"));
  const selectedSection = params.get("section") === "findings" ? "findings" : "grades";
  const requestedCriterion = selectedCriterionId(params.get("criterion"));
  const pendingFocus = useRef<{ target: string; claim: FocusContinuation } | undefined>(undefined);
  useLayoutEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    const target = document.getElementById(pending.target);
    if (!target || target.closest("[hidden]")) return;
    pendingFocus.current = undefined;
    if (pending.claim.focus(target, { preventScroll: true })) target.scrollIntoView({ block: "start" });
  });
  useEffect(() => () => pendingFocus.current?.claim.dispose(), []);
  const follow = (trigger: HTMLElement, target: string) => {
    pendingFocus.current?.claim.dispose();
    pendingFocus.current = { target, claim: claimFocusContinuation(trigger) };
  };
  function focusSharedSection(event: MouseEvent<HTMLAnchorElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    follow(event.currentTarget, event.currentTarget.hash.slice(1));
  }
  const sectionSearch = (section: "grades" | "findings", criterion?: number) => {
    const next = new URLSearchParams(params);
    next.set("section", section);
    if (criterion !== undefined) next.set("criterion", String(criterion));
    return next;
  };
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
  const manifest = !evidence.isError && evidence.fetchStatus === "idle"
    && evidence.data?.assessment_revision === report.assessment_revision
    && Number.isInteger(report.assessment_revision) ? evidence.data : undefined;
  const moduleFlags = flags.filter(flag => flag.check_kind === selectedModule);
  const details = data.detail_snapshot === "current" ? data.finding_details ?? [] : [];
  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  const pendingCount = report.results.filter(isUnresolvedSharedCriterion).length;
  const scopeUnconfirmed = unconfirmedAutomaticRuleRows(report.results, true).length > 0;
  const requestedIndex = report.results.findIndex(row => row.criterion_id === requestedCriterion);
  const selectedIndex = requestedIndex < 0 ? 0 : requestedIndex;
  const selectedCriterion = report.results[selectedIndex];
  const selectCriterion = (trigger: HTMLElement, index: number) => {
    const row = report.results[index];
    if (!row) return;
    follow(trigger, criterionRecordId(report.check_run_id, row.criterion_id));
    setParams(sectionSearch("grades", row.criterion_id));
  };

  return (
    <SharedChrome>
      <div className="signal-shared-route signal-page-flow">
        <header className="signal-route-header signal-shared-report-header"><div>
          <p className="signal-eyebrow">Shared readiness report</p>
          <h1 ref={headingRef} tabIndex={-1}>{report.subject?.basis === "confirmed_assignment_at_check" && report.subject.title ? report.subject.title : "Readiness report"}</h1>
          <p className="signal-route-header__intro">Required format: {report.rubric_title}</p>
          {report.subject?.basis === "confirmed_assignment_at_check" ? <><p>Group members: {report.subject.member_names.join(", ") || "Not recorded"}.</p><p className="signal-field-hint">Recorded when this check began.</p></> : <p>{identity.primary}</p>}
        </div></header>
        {data.detail_snapshot === "stale" && <Alert title="Additional shared detail needs a new link" tone="info">The assessment changed after these details were shared. Ask the instructor to regenerate the link for the current explanations and identity.</Alert>}

        <SharedAssessmentContext report={report} />

        <section className="signal-shared-verdict" aria-labelledby="shared-band-heading"><div><p className="signal-section-kicker">{scopeUnconfirmed ? "Saved readiness band" : "VERIDICAL readiness band"}</p><h2 id="shared-band-heading">{report.status === "ready" ? "Ready" : report.status === "conditionally_ready" ? "Conditionally Ready" : report.status === "not_ready" ? "Not Ready" : "Needs Review"}</h2><p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity signals. It does not approve or block a defense."}</p><RuleScopeSummary rows={report.results} checkRunId={report.check_run_id} publicView linkToRecords={false} /></div><dl><div><dt>Unresolved criteria</dt><dd>{pendingCount}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div></dl><div className="signal-control-cluster"><ActionLink to={{ pathname: location.pathname, search: sectionSearch("grades").toString(), hash: "criteria-results-heading" }} onClick={focusSharedSection} variant="secondary" className="signal-on-dark">Review criterion records</ActionLink><ActionLink to={{ pathname: location.pathname, search: location.search, hash: "shared-decision-heading" }} onClick={focusSharedSection} variant="quiet" className="signal-on-dark">Read final decision</ActionLink></div></section>

        <nav className="signal-control-cluster" aria-label="Shared report sections">{(["grades", "findings"] as const).map(section => <Button key={section} variant={selectedSection === section ? "brand" : "secondary"} aria-pressed={selectedSection === section} onClick={event => {
          follow(event.currentTarget, section === "grades" ? "shared-grade-navigation-heading" : "shared-findings-heading");
          setParams(sectionSearch(section));
        }}>{section === "grades" ? "Grades" : "Findings"}</Button>)}</nav>
        {selectedSection === "grades" ? <section aria-labelledby="shared-grade-navigation-heading">
          <h2 id="shared-grade-navigation-heading" tabIndex={-1}>Grades</h2>
          <p>Choose a criterion to read its recorded assessment and saved evidence.</p>
          {params.has("criterion") && requestedIndex < 0 && <p role="status">Requested criterion is unavailable. Showing the first shared criterion.</p>}
          <nav aria-label="Choose a shared criterion" className="signal-shared-criterion-choices">{report.results.map((row, index) => <Button key={row.criterion_id} variant="secondary" aria-pressed={index === selectedIndex} aria-controls="shared-selected-criterion" onClick={event => selectCriterion(event.currentTarget, index)}><strong>{row.text}</strong><span>{resultDisplay(row).label}</span></Button>)}</nav>
          {selectedCriterion ? <>
            <nav aria-label="Shared criterion navigation" className="signal-control-cluster">
              <Button variant="secondary" disabled={selectedIndex === 0} onClick={event => selectCriterion(event.currentTarget, selectedIndex - 1)}>Previous criterion</Button>
              <span role="status">Criterion {selectedIndex + 1} of {report.results.length}</span>
              <Button variant="secondary" disabled={selectedIndex >= report.results.length - 1} onClick={event => selectCriterion(event.currentTarget, selectedIndex + 1)}>Next criterion</Button>
            </nav>
            <div id="shared-selected-criterion" key={`${token}:${report.assessment_revision}:${manifest?.snapshot_id}:${selectedCriterion.criterion_id}`}>
              <SignalCriteriaResults results={[selectedCriterion]} checkRunId={report.check_run_id} publicView publicSelection={{ position: selectedIndex + 1, total: report.results.length }} renderEvidence={row => <SharedEvidencePacket key={`${token}:${manifest?.snapshot_id}:${row.criterion_id}`} token={token ?? ""} manifest={manifest} references={manifest?.references.filter(ref => ref.criterion_id === row.criterion_id) ?? []} fallback={row.evidence} context={{title: row.text, explanation: row.reasoning, recordedNote: row.reason}} />} />
            </div>
          </> : <p>No criterion records were shared.</p>}
        </section> : <section aria-labelledby="shared-findings-heading">
        <h2 id="shared-findings-heading" tabIndex={-1}>Findings</h2>
        <IntegrityModuleNavigation selected={selectedModule} controls="shared-selected-module" modules={report.integrity_modules}
          counts={new Map(["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"].map(kind => [kind, flags.filter(flag => flag.check_kind === kind).length]))}
          onSelect={kind => { const next = new URLSearchParams(params); next.set("module", kind); setParams(next, { replace: true }); }} />
        <div id="shared-selected-module">
          <SignalPublicFlags flags={moduleFlags} selectedModule={selectedModule} originalityScope={selectedModule === "originality_reuse" ? report.originality_scope : null} checkRunId={report.check_run_id} statisticalScope={selectedModule === "statistical_forensics" ? report.statistical_check_scope : null} />
          {moduleFlags.map(flag => {
            const detail = details.find(saved => saved.id === flag.id);
            if (!detail && !manifest?.references.some(ref => ref.flag_id === flag.id)) return null;
            return <article className="signal-shared-packet" key={flag.id} aria-label={`Saved finding detail ${flag.id}`}>
              <h3>Saved finding record #{flag.id}</h3>
              <div className="signal-shared-packet__explanation"><p>{detail?.ai_reasoning ?? "No additional system explanation was saved with this link."}</p></div>
              {detail?.p_value_comparison && <dl><div><dt>Reported in manuscript</dt><dd>{detail.p_value_comparison.reported}</dd></div><div><dt>Recalculated by the check</dt><dd>{detail.p_value_comparison.recalculated}</dd></div></dl>}
              {detail?.citation_source_locator && <CitationSourceLinks flag={{ check_kind: flag.check_kind, ai_verdict_summary: detail.ai_verdict_summary, citation_source_locator: detail.citation_source_locator }} />}
              {detail && <CitationComparison flag={{ check_kind: flag.check_kind, ai_verdict_summary: detail.ai_verdict_summary, citation_metadata_comparison: detail.citation_metadata_comparison, citation_metadata_review_warning: detail.citation_metadata_review_warning }} />}
              <SharedEvidencePacket token={token ?? ""} manifest={manifest} references={manifest?.references.filter(ref => ref.flag_id === flag.id) ?? []}
                fallback={detail?.agreement_pair ? [{ quote: detail.agreement_pair.intent_text, anchor: detail.agreement_pair.intent_anchor ?? "" }, { quote: detail.agreement_pair.outcome_text, anchor: detail.agreement_pair.outcome_anchor ?? "" }]
                  : detail?.passage_pair ? [{ quote: detail.passage_pair.own_excerpt, anchor: flag.page_anchor }, { quote: detail.passage_pair.matched_excerpt, anchor: "Saved comparison excerpt" }]
                    : [{ quote: flag.evidence_excerpt, anchor: flag.page_anchor }]} />
              {detail?.passage_pair && <section aria-label="Saved passage context">
                {detail.passage_pair.own_context_before && <p>Context before the checked passage: {detail.passage_pair.own_context_before}</p>}
                {detail.passage_pair.own_context_after && <p>Context after the checked passage: {detail.passage_pair.own_context_after}</p>}
                {detail.passage_pair.matched_context_before && <p>Context before the comparison excerpt: {detail.passage_pair.matched_context_before}</p>}
                {detail.passage_pair.matched_context_after && <p>Context after the comparison excerpt: {detail.passage_pair.matched_context_after}</p>}
              </section>}
            </article>;
          })}
        </div>
        </section>}

        <section className="signal-shared-decision" aria-labelledby="shared-decision-heading"><div><p className="signal-section-kicker">Instructor authority</p><h2 id="shared-decision-heading" tabIndex={-1}>Final decision</h2></div>{report.decision ? <><span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span><p>Recorded by the instructor. VERIDICAL did not make this decision.</p>{report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}</> : <><p>No final instructor decision has been recorded.</p><p>The readiness band above is an aid for review, not an approval or block.</p></>}</section>
      </div>
    </SharedChrome>
  );
}
