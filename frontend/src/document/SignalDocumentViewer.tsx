import { criterionSourceReturn } from "../report/criterionSourceReturn";
import { PdfSourceReader } from "./PdfSourceReader";
import { clearPdfReading, sameSourceTask } from "./usePdfPageText";
import { useQueryClient } from "@tanstack/react-query";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { type KeyboardEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams, useSearchParams } from "react-router";
import { ApiError, BASE_URL } from "../api/client";
import type { FlagOut, FlagRegionOut, ReportOut } from "../api/types";
import { CRITERION_ANCHOR_REGION_ID, DOCUMENT_SINGLE_PANE_MEDIA_QUERY, COMPARISON_LEAD_PAGE_SIZE } from "../config/ui";
import { checkKindMeta, findingVerificationPrompt, isPValueFinding } from "../domain/checkKind";
import { DECISION_LABEL } from "../domain/decisionTone";
import { agreementEvidenceType, evidenceDisplayState } from "../domain/evidenceDisplay";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { problemLabel } from "../domain/problemLabel";
import { severityLabel } from "../domain/severity";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { instructorAnchor } from "../domain/sourceAnchor";
import { localDate } from "../domain/localDate";
import { useMe } from "../auth/useAuth";
import { useViewedFlagIds } from "../domain/flagViewed";
import { COMPARISON_LEAD_COPY, COMPARISON_LEAD_HEADING_ID, comparisonLeadFilter, comparisonLeadIdentity, isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { ComparisonLeadList } from "../report/ComparisonLeadList";
import { comparisonLeadCollection, type ComparisonLeadCollection } from "../report/comparisonLeadCollection";
import { FigureReadingsSummary } from "../report/FigureReadings";
import { useFlag } from "../flags/useFlag";
import { PValueComparison } from "../flags/PValueComparison";
import { AgreementComparison } from "../flags/AgreementComparison";
import { CitationComparison } from "../flags/CitationComparison";
import { CitationHistoryWarning } from "../flags/CitationHistoryWarning";
import { findingMethodLabel, isCitationMetadataFinding } from "../domain/findingMethod";
import { SignalDecisionPanel } from "../report/SignalDecisionPanel";
import { RuleScopeSummary } from "../report/RuleScopeNotice";
import { unconfirmedAutomaticRuleRows } from "../domain/ruleScope";
import { ReportReadNotice } from "../report/ReportReadNotice";
import { recoverableReadError, usableReport } from "../report/reportReadState";
import { SignalCriteriaResults, SignalEscalatedPanel, type CriterionSourceOpen, type CriterionSourceTarget } from "../report/SignalReviewSections";
import { coverageItems } from "../report/reportCoverage";
import {
  useExcludedReuseMatches,
  useFlags,
  useManuscriptParagraphs,
  useManuscriptViewer,
  useReport,
} from "../report/useReport";
import { clearRouteReturnFocus, rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { criterionRecordId, revealCriterionRecord, SAVED_CRITERION_PARAM } from "../report/savedJudgment";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { CoverageStatement } from "../ui/CoverageStatement";
import { useMediaQuery } from "../ui/useMediaQuery";
import { ReadinessBand } from "../ui/ReadinessBand";
import { DocxPane } from "./DocxPane";
import { useSelectedSourceReveal } from "./useSelectedSourceReveal";
import { scrollReviewEvidence } from "./scrollReviewEvidence";
import { PassagePairPanel } from "./PassagePairPanel";
import { regionPrecision, type SourceAvailability } from "./regionCopy";
import {
  buildReviewFindings,
  findingForFlag,
  findingNumberByFlagId,
  representativeRegions,
  type ReviewFinding,
} from "./reviewFindings";
import { ReuseExplorePanel } from "./ReuseExplorePanel";

type ReviewFilter = "open" | "resolved" | "all";

function criterionAnchorRegion(anchor: string | null): FlagRegionOut | null {
  if (!anchor) return null;
  const page = anchor.trim().match(/^(?:page|p\.)\s*(\d+)$/i);
  if (page && Number.isSafeInteger(Number(page[1])) && Number(page[1]) > 0) {
    return {
      flag_id: CRITERION_ANCHOR_REGION_ID,
      kind: "page_only",
      page: Number(page[1]),
      end_page: null,
      bbox: null,
      all_bboxes: [],
      paragraph: null,
      index: null,
    };
  }
  const paragraph = anchor.trim().match(/^(?:¶|\u00c2¶|paragraph\s+)(\d+)$/i);
  if (paragraph && Number.isSafeInteger(Number(paragraph[1]))) {
    return {
      flag_id: CRITERION_ANCHOR_REGION_ID,
      kind: "paragraph_only",
      page: null,
      end_page: null,
      bbox: null,
      all_bboxes: [],
      paragraph: Number(paragraph[1]),
      index: null,
    };
  }
  return null;
}

function parseFilter(value: string | null): ReviewFilter {
  return value === "resolved" || value === "all" ? value : "open";
}

function findingTitle(finding: ReviewFinding): string {
  const flag = finding.representative;
  return problemLabel(flag.problem_kind) ?? flag.criterion_text ?? checkKindMeta(flag.check_kind).title;
}

function regionForFinding(finding: ReviewFinding, regions: FlagRegionOut[]): FlagRegionOut | null {
  return regions.find((region) => region.flag_id === finding.representative.id)
    ?? finding.flags.map((flag) => regions.find((region) => region.flag_id === flag.id)).find(Boolean)
    ?? null;
}

function ReviewQueue({ findings, regions, sourceAvailability, report, collection, viewedIds, filter, invalidSelection, onFilter, onSelect, onExplore, onDecision, onRetry }: {
  findings: ReviewFinding[];
  regions: FlagRegionOut[];
  sourceAvailability?: SourceAvailability;
  report: ReportOut;
  collection: ComparisonLeadCollection;
  viewedIds: ReadonlySet<number>;
  onRetry: () => void;
  filter: ReviewFilter;
  invalidSelection: boolean;
  onFilter: (filter: ReviewFilter) => void;
  onSelect: (id: number) => void;
  onExplore: () => void;
  onDecision: (target: "criteria" | "readiness") => void;
}) {
  const open = findings.filter((finding) => !finding.isResolved);
  const resolved = findings.filter((finding) => finding.isResolved);
  const visible = filter === "open" ? open : filter === "resolved" ? resolved : findings;

  return (
    <div className="signal-document-review-queue">
      {invalidSelection && <Alert title="That finding is no longer available" tone="warning" role="status">The review queue is open.</Alert>}
      {report.pending_review_count > 0 && (
        <section className="signal-document-criterion-task" aria-labelledby="criterion-task-heading">
          <div><h2 id="criterion-task-heading">{report.pending_review_count} {report.pending_review_count === 1 ? "criterion needs" : "criteria need"} your judgment</h2></div>
          <Button variant="secondary" onClick={() => onDecision("criteria")}>Review criteria</Button>
        </section>
      )}
      {findings.length > 0 && (
        <div className="signal-document-inspector__heading"><h2>{collection.lastLoaded ? "Last loaded integrity findings" : "Integrity findings"}</h2></div>
      )}
      {findings.length > 0 && (
        <div className="signal-document-review-filters" role="group" aria-label="Filter review findings">
          {(["open", "resolved", "all"] as const).map((value) => {
            const count = value === "open" ? open.length : value === "resolved" ? resolved.length : findings.length;
            const label = value[0].toUpperCase() + value.slice(1);
            return <button key={value} type="button" aria-pressed={filter === value} onClick={() => onFilter(value)}>{collection.state !== "success" ? "Last loaded: " : ""}{label} {count}</button>;
          })}
        </div>
      )}
      {findings.length > 0 && (visible.length === 0 && filter === "open" ? (
        <Alert title={collection.state !== "success" ? "Last loaded: no open integrity findings" : "No open integrity findings"} tone="info"><p>{collection.state !== "success" ? "Every other finding in the last loaded records has an instructor resolution. Current records have not been confirmed." : "Every recorded integrity finding has an instructor resolution. You can review resolved findings or continue to readiness and decision."}</p><Button variant="secondary" onClick={() => onFilter("resolved")}>Show resolved findings</Button></Alert>
      ) : (
        <ol className="signal-document-review-list">
          {visible.map((finding) => {
            const precision = regionPrecision(regionForFinding(finding, regions), sourceAvailability);
            const state = finding.isSourceConfirmed ? "Source confirmed" : finding.isResolved ? "Resolved" : "Open";
            return (
              <li key={finding.key}>
                <button id={`review-finding-${finding.representative.id}`} type="button" onClick={() => onSelect(finding.representative.id)}>
                  <span className="signal-document-finding-number">Finding {finding.number}</span>
                  <strong>{findingTitle(finding)}</strong>
                  <span className="signal-document-finding-meta">{severityLabel(finding.severity)} · {instructorAnchor(finding.representative.page_anchor)} · {precision.label}</span>
                  {finding.flags.length > 1 && <span className="signal-document-finding-locations">{finding.flags.length} recorded locations or scopes</span>}
                  <span className="signal-document-finding-state">{state}</span>
                </button>
              </li>
            );
          })}
        </ol>
      ))}
      <ComparisonLeadList collection={collection} workspace={findings.length === 0 && report.pending_review_count === 0} headingLevel={2} showActions viewedIds={viewedIds} onSelect={onSelect} onRetry={onRetry} />
      <Button variant="quiet" onClick={onExplore}>Other passage matches not included in readiness</Button>
      <section className="signal-document-readiness-card" aria-labelledby="readiness-card-heading">
        <p className="signal-section-kicker">After the evidence</p>
        <h3 id="readiness-card-heading">Readiness and decision</h3>
        <dl>
          <div><dt>System readiness</dt><dd><ReadinessBand status={report.status} /></dd></div>
          <div><dt>Criteria needing judgment</dt><dd>{report.pending_review_count}</dd></div>
          <div><dt>Open integrity findings</dt><dd>{collection.state === "success" ? open.length : findings.length > 0 ? `Last loaded: ${open.length}` : "Not confirmed"}</dd></div>
          <div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div>
        </dl>
        <div className="signal-document-readiness-card__actions"><Button variant="secondary" onClick={() => onDecision("readiness")}>Review readiness and decision</Button><ActionLink to={`/report/${report.check_run_id}`} variant="quiet">Open full readiness report</ActionLink></div>
      </section>
    </div>
  );
}

function SelectedFinding({ finding, total, locationFlagId, region, sourceAvailability, sourceAnchor, previous, next, collection, outsideFilter, filterPosition, filterTotal, onAll, onBack, onPrevious, onNext, onLocation, onExplore, fullWidth, singlePane, onReadingChange }: {
  finding: ReviewFinding; total: number; locationFlagId: number; region: FlagRegionOut | null;
  sourceAvailability?: SourceAvailability; sourceAnchor: string | null;
  previous: ReviewFinding | null; next: ReviewFinding | null;
  collection: ComparisonLeadCollection; outsideFilter: boolean; filterPosition: number; filterTotal: number;
  onAll: () => void; onBack: () => void; onPrevious: () => void; onNext: () => void;
  onLocation: (flagId: number) => void; onExplore: () => void;
  fullWidth: boolean; singlePane: boolean; onReadingChange: (expanded: boolean) => void;
}) {
  const query = useFlag(locationFlagId);
  const client = useQueryClient();
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const detail = query.data?.id === locationFlagId && query.data.check_run_id === Number(checkRunId)
    && recoverableReadError(query.error) ? query.data : undefined;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const readingBarRef = useRef<HTMLDivElement>(null);
  const optional = isOptionalComparisonLead(finding.representative);
  const identity = comparisonLeadIdentity(finding.representative, collection.snapshot);
  const qualified = collection.snapshot?.ordinalById.has(finding.representative.id) === true;
  const navigationWasShown = useRef(false);
  if (optional && (collection.snapshot?.total ?? 0) > 1) navigationWasShown.current = true;
  const canPrevious = Boolean(previous && (!optional || (qualified && !outsideFilter)));
  const canNext = Boolean(next && (!optional || (qualified && !outsideFilter)));
  useLayoutEffect(() => {
    const bar = readingBarRef.current;
    const inspector = bar?.closest<HTMLElement>("#review-panel");
    if (!fullWidth || !bar || !inspector) return;
    const measure = () => inspector.style.setProperty("--signal-reading-bar-height", `${bar.getBoundingClientRect().height}px`);
    measure();
    const observer = new ResizeObserver(measure); observer.observe(bar);
    return () => { observer.disconnect(); inspector.style.removeProperty("--signal-reading-bar-height"); };
  }, [fullWidth]);
  function move(allowed: boolean, action: () => void) {
    if (!allowed) return;
    if (document.hasFocus()) headingRef.current?.focus({ preventScroll: true });
    action();
  }
  function retryComparisonList(event: React.MouseEvent<HTMLButtonElement>) {
    if (document.hasFocus() && document.activeElement === event.currentTarget) {
      const target = fullWidth ? document.getElementById("evidence-heading") ?? headingRef.current : headingRef.current;
      target?.focus({ preventScroll: true });
      const inspector = target?.closest<HTMLElement>("#review-panel");
      if (target?.id === "evidence-heading" && inspector) scrollReviewEvidence(target, inspector);
    }
    void client.invalidateQueries({ queryKey: ["report", Number(checkRunId), "flags"], exact: true });
  }
  return <article className="signal-document-selected" aria-labelledby="selected-finding-heading">
    <p role="status" aria-live="polite" className="sr-only">{fullWidth ? "Review expanded to full width." : ""}</p>
    {fullWidth && <div ref={readingBarRef} className="signal-comparison-reading-bar"><div><strong>{identity.marker} · Record #{locationFlagId}</strong><span>{instructorAnchor(sourceAnchor ?? finding.representative.page_anchor)}</span></div><Button id="comparison-reading-return" variant="secondary" onClick={() => onReadingChange(false)}>{singlePane ? "Return to manuscript review" : "Return to split review"}</Button></div>}
    <Button variant="quiet" onClick={onBack}>Back to review queue</Button>
    <header className="signal-document-selected__header">
      <p className="signal-section-kicker">{optional ? `${collection.lastLoaded ? "Last loaded: " : ""}${identity.label}` : `Finding ${finding.number} of ${total}`}</p>
      <h2 id="selected-finding-heading" ref={headingRef} tabIndex={-1}>{findingTitle(finding)}</h2>
      <div className="signal-document-selected__states"><span className={optional ? "signal-comparison-status" : `signal-severity signal-severity--${finding.severity}`}>{optional ? COMPARISON_LEAD_COPY.badge : severityLabel(finding.severity)}</span><span className="signal-resolution-state">{finding.isResolved ? "Resolved" : "Open"}</span></div>
      {optional && outsideFilter && <><p>{COMPARISON_LEAD_COPY.outsideFilter}</p><Button variant="quiet" onClick={onAll}>Show all comparison leads</Button></>}
      {optional && qualified && !outsideFilter && filterTotal !== collection.snapshot?.total && filterPosition > 0 && <p>Item {filterPosition} of {filterTotal} in this filter.</p>}
      {(optional ? navigationWasShown.current : previous || next) && <div className="signal-document-finding-nav" role="group" aria-label={optional ? "Move between comparison leads" : "Move between review findings"}>
        <Button variant="quiet" disabled={optional ? undefined : !canPrevious} aria-disabled={optional ? !canPrevious : undefined} onClick={() => move(canPrevious, onPrevious)}>{optional ? "Previous comparison lead" : "Previous finding"}</Button>
        <Button variant="quiet" disabled={optional ? undefined : !canNext} aria-disabled={optional ? !canNext : undefined} onClick={() => move(canNext, onNext)}>{optional ? "Next comparison lead" : "Next finding"}</Button>
      </div>}
    </header>
    {optional && <p>{COMPARISON_LEAD_COPY.intro}</p>}
    {optional && collection.lastLoaded && <div role="status" className="signal-field-hint">{collection.state === "error" ? "The comparison list could not be refreshed. Showing last loaded records." : collection.state === "paused" ? "Waiting for a connection. Showing last loaded comparison records." : "Updating comparison records. Showing last loaded records."}{collection.state === "error" && <Button variant="quiet" onClick={retryComparisonList}>Retry comparison list</Button>}</div>}
    {detail && query.isError && <Alert title="These details could not be refreshed" tone="warning"><p>Showing the last loaded record. Check its source before drawing a conclusion.</p><Button variant="quiet" onClick={() => { void query.refetch(); }}>Try again</Button></Alert>}
    {query.isPending && !detail ? <div role="status" aria-live="polite" aria-busy={query.fetchStatus === "fetching"} className="signal-desk-loading"><span>{query.fetchStatus === "paused" ? "Waiting to load" : "Loading"} {optional ? "comparison lead record" : `Finding ${finding.number}`}.</span><i /><i /></div>
      : !detail ? <Alert title={optional ? "Comparison lead details could not be loaded" : "Finding details could not be loaded"} tone="error" role="alert"><p>The manuscript and review queue are still available. Try loading this record again.</p><Button variant="secondary" onClick={() => { if (document.hasFocus()) headingRef.current?.focus({ preventScroll: true }); void query.refetch(); }}>Try again</Button></Alert>
        : <SelectedFindingEvidence finding={finding} flag={detail} locationFlagId={locationFlagId} region={region} sourceAvailability={sourceAvailability} sourceAnchor={sourceAnchor} onLocation={onLocation} onExplore={onExplore} fullWidth={fullWidth} canExpand={optional && !singlePane} onExpand={() => onReadingChange(true)} />}
  </article>;
}

function SelectedFindingEvidence({ finding, flag, locationFlagId, region, sourceAvailability, sourceAnchor, onLocation, onExplore, fullWidth, canExpand, onExpand }: {
  finding: ReviewFinding; flag: FlagOut; locationFlagId: number; region: FlagRegionOut | null;
  sourceAvailability?: SourceAvailability; sourceAnchor: string | null;
  onLocation: (flagId: number) => void; onExplore: () => void;
  fullWidth: boolean; canExpand: boolean; onExpand: () => void;
}) {
  const reviewLocation = useLocation();
  const { showsManuscriptQuote, reasoningText, showsManuscriptEvidence, isResubmission } = evidenceDisplayState(flag);
  const precision = regionPrecision(region, sourceAvailability);
  const comparative = flag.check_kind === "originality_reuse";
  const optional = isOptionalComparisonLead(flag);
  const wholeComparisonWithoutPair = optional && !flag.is_passage_level && !flag.passage_pair && !flag.evidence_unavailable;
  const kindMeta = checkKindMeta(flag.check_kind);
  const verificationPrompt = findingVerificationPrompt(flag.check_kind, flag.ai_verdict_summary, !!flag.passage_pair, !!flag.citation_metadata_comparison);
  const pValueFinding = flag.check_kind === "statistical_forensics" && isPValueFinding(flag.ai_verdict_summary);
  const citationMetadata = isCitationMetadataFinding(flag);
  const title = findingTitle(finding);
  const locationIndex = finding.flags.findIndex((member) => member.id === locationFlagId);

  return <>
      {!optional && <dl className="signal-document-finding-facts">
        <div><dt>Check</dt><dd>{kindMeta.eyebrow}</dd></div>
        <div><dt>How exact is this location?</dt><dd>{precision.label}</dd></div>
        <div><dt>Recorded location</dt><dd>{(sourceAnchor ?? flag.page_anchor).trim() ? instructorAnchor(sourceAnchor ?? flag.page_anchor) : "No location was recorded."}</dd></div>
        <div><dt>Check method</dt><dd>{findingMethodLabel(flag)}</dd></div>
        <div><dt>Evidence type</dt><dd>{citationMetadata ? flag.citation_metadata_comparison ? "Recorded registry metadata comparison" : "Registry comparison without saved provider metadata" : flag.check_kind === "internal_agreement" ? agreementEvidenceType(flag) : comparative ? flag.passage_pair ? "Recorded passage comparison" : "Comparison without a stored pair" : "Single-location check"}</dd></div>
      </dl>}
      {wholeComparisonWithoutPair
        ? <p className="signal-document-precision-note"><strong>Whole-manuscript comparison.</strong> No passage pair was saved.</p>
        : <p className="signal-document-precision-note"><strong>{precision.label}.</strong> {precision.helper}{optional && flag.is_passage_level && <span> Recorded location: {instructorAnchor(sourceAnchor ?? flag.page_anchor)}.</span>}</p>}
      {finding.flags.length > 1 && (
        <section className="signal-document-locations" aria-labelledby="finding-locations-heading">
          <h3 id="finding-locations-heading">Recorded locations and scopes</h3>
          <p>Location {locationIndex + 1} of {finding.flags.length}. These records are presented as one finding; none of the stored data was removed.</p>
          <div role="group" aria-label={`Locations for Finding ${finding.number}`}>{finding.flags.map((member, index) => <button key={member.id} type="button" aria-pressed={member.id === locationFlagId} onClick={() => onLocation(member.id)}>Location {index + 1}: {instructorAnchor(member.page_anchor)}</button>)}</div>
        </section>
      )}
      {!isOptionalComparisonLead(flag) && <section aria-labelledby="noticed-heading" className="signal-document-detail-section">
        <h3 id="noticed-heading">What VERIDICAL noticed</h3>
        <p>{title}. {isOptionalComparisonLead(flag) ? "This is a saved comparison lead for instructor inspection." : "This is a possible issue for instructor review, not a final determination."}</p>
        {!showsManuscriptQuote && <p><strong>Recorded check summary:</strong> {systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}</p>}
      </section>}
      {isOptionalComparisonLead(flag) && !showsManuscriptQuote && <p><strong>Recorded check result:</strong> {systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}</p>}
      <CitationHistoryWarning flag={flag} />
      {!wholeComparisonWithoutPair && <section aria-labelledby="evidence-heading" className="signal-document-detail-section">
        <h3 id="evidence-heading" tabIndex={-1}>Evidence to compare or check</h3>
        {flag.passage_pair && canExpand && !fullWidth && <Button id={`comparison-reading-expand-${locationFlagId}`} variant="secondary" onClick={onExpand}>Review in full width</Button>}
        {flag.passage_pair && <PassagePairPanel pair={flag.passage_pair} ownAnchor={instructorAnchor(flag.page_anchor)} wholeManuscript={!flag.is_passage_level} variant="flag" placement={fullWidth ? "full-page" : "inspector"} />}
        {!flag.passage_pair && showsManuscriptQuote && flag.check_kind !== "internal_agreement" && <blockquote><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{instructorAnchor(flag.page_anchor)}</cite></blockquote>}
        <PValueComparison flag={flag} />
        <AgreementComparison flag={flag} findingNumber={finding.number} />
        <CitationComparison flag={flag} />
        {flag.ai_verdict_summary === "orphan_in_text_citation" ? <p className="signal-field-hint">The saved check did not link this citation to a reference-list entry. A matching entry is not available for side-by-side comparison.</p> : !citationMetadata && !comparative && !pValueFinding && flag.check_kind !== "internal_agreement" && <p className="signal-field-hint">This check evaluates one manuscript location. A second passage does not apply.</p>}
      </section>}
      {flag.passage_pair && <Button variant="quiet" onClick={onExplore}>See other passage matches</Button>}
      {!showsManuscriptEvidence && !wholeComparisonWithoutPair && (
        <Alert title={isResubmission ? "No passage pair is available" : "No supporting passage was stored"} tone={isResubmission ? "info" : "warning"}>
          {isResubmission
            ? "This finding was recorded from a whole-manuscript check. VERIDICAL did not store two passages that can be compared side by side. Review the recorded scope and technical details before deciding."
            : flag.evidence_unavailable
              ? "VERIDICAL could not show a passage for this finding. Its severity was reduced. Do not treat this as source-verified evidence."
              : "VERIDICAL did not store two passages that can be compared side by side. Review the recorded scope and technical details before deciding."}
        </Alert>
      )}
      {!wholeComparisonWithoutPair && <section aria-labelledby="verify-heading" className="signal-document-detail-section signal-document-detail-section--verify"><h3 id="verify-heading">What the instructor should verify</h3><p>{verificationPrompt}</p></section>}
      {(!pValueFinding || flag.p_value_comparison) && <details className="signal-document-recorded-reasoning"><summary>{flag.citation_metadata_review_warning ? "Original recorded explanation and technical details" : "Recorded reasoning and technical details"}</summary>
        {optional && <dl className="signal-document-finding-facts">
          <div><dt>Check</dt><dd>{kindMeta.eyebrow}</dd></div>
          <div><dt>Check method</dt><dd>{findingMethodLabel(flag)}</dd></div>
          <div><dt>Evidence type</dt><dd>{flag.passage_pair ? "Recorded passage comparison" : "Comparison without a stored pair"}</dd></div>
          <div><dt>Recorded location</dt><dd>{(sourceAnchor ?? flag.page_anchor).trim() ? instructorAnchor(sourceAnchor ?? flag.page_anchor) : "No location was recorded."}</dd></div>
        </dl>}
        <p>This is the system's full recorded explanation. It is supporting information, not an instructor decision.</p>{reasoningText ? <p>{systemFindingCopy(reasoningText, flag.ai_verdict_summary, "reasoning")}</p> : <p>No additional recorded reasoning is available.</p>}</details>}
      <ActionLink id={`comparison-lead-actions-${flag.id}`} to={`/flags/${flag.id}`} variant="secondary"
        state={{ routeReturnFocus: { returnPath: reviewLocation.pathname + reviewLocation.search, elementId: `comparison-lead-actions-${flag.id}`, fallbackElementId: "selected-finding-heading" } }}>Review instructor actions for this location</ActionLink>
  </>;
}

function ReportUnavailable({ checkRunId, error, onRetry }: { checkRunId: number; error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.code === "conflict") return <Alert title="This review is not ready yet" tone="info" role="alert"><p>The check has not produced a complete readiness record.</p><ActionLink to={`/checks/${checkRunId}`} variant="secondary">View check progress</ActionLink></Alert>;
  return <Alert title="Could not load review details" tone="error" role="alert"><p>VERIDICAL could not load the recorded review. The source pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={onRetry}>Try review again</Button><ReviewDeskReturnLink variant="quiet">Return to Review Desk</ReviewDeskReturnLink></Alert>;
}

function ReadinessDecision({ checkRunId, report: loadedReport, pending, error, retrying, onRetry, onBack, sourceTarget, onOpenSource }: { checkRunId: number; report: ReportOut | undefined; pending: boolean; retrying: boolean; error: unknown; onRetry: () => void; onBack: () => void; sourceTarget: CriterionSourceTarget; onOpenSource: CriterionSourceOpen }) {
  const report = usableReport(loadedReport, checkRunId, error);
  const readFailed = Boolean(report && error);
  if (pending && !report) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading readiness and decision.</span><i /><i /></div>;
  if (!report) return <ReportUnavailable checkRunId={checkRunId} error={error} onRetry={onRetry} />;

  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  const coverage = coverageItems(report);
  const criterionRecords = report.results.filter((row) => row.outcome !== "escalated");
  return (
    <div className="signal-document-analysis">
      <Button variant="quiet" onClick={onBack}>Back to review queue</Button>
      {readFailed && <ReportReadNotice retrying={retrying} onRetry={onRetry} />}
      <SignalEscalatedPanel checkRunId={report.check_run_id} sourceTarget={sourceTarget} onOpenSource={onOpenSource} />
      <details className="signal-document-analysis__criteria"><summary>Recorded criterion results, {criterionRecords.length} {criterionRecords.length === 1 ? "entry" : "entries"}</summary><SignalCriteriaResults results={criterionRecords} checkRunId={report.check_run_id} sourceTarget={sourceTarget} onOpenSource={onOpenSource} /></details>
      <section className="signal-document-analysis__summary" aria-labelledby="analysis-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
        <div className="signal-document-inspector__heading"><p className="signal-section-kicker">After the evidence</p><h2 id="analysis-heading" tabIndex={-1}>Readiness and decision</h2><p>Use this summary after reviewing the evidence. The instructor records the final decision.</p></div>
        <div className="signal-document-analysis__readiness">
          <div><span>{readFailed ? "Last loaded system readiness" : unconfirmedAutomaticRuleRows(report.results).length ? "Saved readiness band" : "System readiness"}</span><ReadinessBand status={report.status} /></div>
          <p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity findings."}</p>
          <RuleScopeSummary rows={report.results} checkRunId={report.check_run_id} />
          <dl><div><dt>Criteria needing your judgment</dt><dd>{report.pending_review_count}</dd></div><div><dt>Open high-severity findings</dt><dd>{report.unresolved_high_flag_count}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div></dl>
          <p className="signal-field-hint">{identity.primary} was checked against {report.rubric_title}.</p>
          <ActionLink to={`/report/${report.check_run_id}`} variant="secondary">Open full readiness report</ActionLink>
        </div>
      </section>
      {coverage.length > 0 && <CoverageStatement state="partial" headingId="workspace-coverage-heading" heading="Assessment limits" intro="Results below cover only the checks that ran. Unchecked content is not treated as passed." items={coverage} />}
      <FigureReadingsSummary readings={report.figure_readings} checkRunId={checkRunId} />
      <SignalDecisionPanel report={report} manuscriptLabel={identity.primary} readFailed={readFailed} retrying={retrying} onRetry={onRetry} />
      <div className="signal-document-analysis__footer"><ActionLink to={`/report/${report.check_run_id}`} variant="secondary">Open full readiness report</ActionLink></div>
    </div>
  );
}

function ReviewHome({ checkRunId, report, reportPending, reportRetrying, reportError, onReportRetry, findings, regions, sourceAvailability, collection, viewedIds, onFlagsRetry, filter, invalidSelection, onFilter, onSelect, onExplore, onDecision }: {
  checkRunId: number;
  report: ReportOut | undefined;
  reportPending: boolean;
  reportRetrying: boolean;
  reportError: unknown;
  onReportRetry: () => void;
  findings: ReviewFinding[];
  regions: FlagRegionOut[];
  sourceAvailability?: SourceAvailability;
  collection: ComparisonLeadCollection;
  viewedIds: ReadonlySet<number>;
  onFlagsRetry: () => void;
  filter: ReviewFilter;
  invalidSelection: boolean;
  onFilter: (filter: ReviewFilter) => void;
  onSelect: (id: number) => void;
  onExplore: () => void;
  onDecision: (target: "criteria" | "readiness") => void;
}) {
  if (reportPending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading review queue.</span><i /><i /></div>;
  const availableReport = usableReport(report, checkRunId, reportError);
  if (!availableReport) return <ReportUnavailable checkRunId={checkRunId} error={reportError} onRetry={onReportRetry} />;
  return <>{Boolean(reportError) && <ReportReadNotice retrying={reportRetrying} onRetry={onReportRetry} />}<ReviewQueue findings={findings} regions={regions} sourceAvailability={sourceAvailability} report={availableReport} collection={collection} viewedIds={viewedIds} onRetry={onFlagsRetry} filter={filter} invalidSelection={invalidSelection} onFilter={onFilter} onSelect={onSelect} onExplore={onExplore} onDecision={onDecision} /></>;
}

export function SignalDocumentViewerPage() {
  const location = useLocation();
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const validId = Number.isInteger(id) && id > 0;
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSelection = searchParams.get("finding") ?? searchParams.get("flag");
  const parsedSelection = rawSelection ? Number(rawSelection) : null;
  const requestedFlagId = parsedSelection && Number.isInteger(parsedSelection) && parsedSelection > 0 ? parsedSelection : null;
  const recordedAnchor = searchParams.get("anchor");
  const parsedAnchorRegion = criterionAnchorRegion(recordedAnchor);
  const view = searchParams.get("view");
  const isExploring = view === "matches" || searchParams.get("panel") === "explore";
  const isDecision = !isExploring && view === "decision";
  const includeReferenceList = searchParams.get("ref") === "1";
  const includeBlockQuote = searchParams.get("quote") === "1";
  const expandedMatchId = searchParams.get("match");
  const activeTab = searchParams.get("pane") === "review" || searchParams.get("pane") === "analysis" ? "review" : "manuscript";
  const filter = parseFilter(searchParams.get("filter"));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const manuscriptTabRef = useRef<HTMLButtonElement>(null);
  const sourcePanelRef = useRef<HTMLDivElement>(null);
  const sourceFocusOwner = useRef<HTMLElement | null>(null);
  const reviewTabRef = useRef<HTMLButtonElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const queueScrollRef = useRef(0);
  const queueFocusRef = useRef<number | null>(null);
  const restoreQueueRef = useRef(false);
  const leadContinuation = useRef<{ run: number; from: string; search: string; targetId: string; ownership: FocusContinuation } | undefined>(undefined);
  const sourceAttemptSequence = useRef(0);
  const [, setSourceRevealAttempt] = useState(0);
  const criterionInspectionRun = useRef<number | null>(null);
  const [criterionSource, setCriterionSource] = useState<{ attempt: number; run: number; anchor: string; origin: string; from: string; destination: string; actionId: string; recordCriterionId?: number; recordIsInstructor?: boolean }>();
  const sourcePlacement = useRef<{ run: number; origin: string; destination: string; adopted: boolean; allowed: boolean } | undefined>(undefined);
  const liveSourceScope = useRef({ run: id, query: searchParams.toString() });
  const returnSourceFocus = useRef<{ from: string; search: string; actionId: string; owner: Element | null; recordCriterionId?: number } | undefined>(undefined);
  const reportOrigin = useMemo(() => criterionSourceReturn(location.state, id), [location.state, id]);

  const viewerQuery = useManuscriptViewer(id);
  const reportQuery = useReport(id);
  const flagsQuery = useFlags(id);
  const flags = recoverableReadError(flagsQuery.error) ? flagsQuery.data : undefined;
  useRouteFocus("Manuscript review - VERIDICAL", headingRef, reportOrigin, { prefix: "comparison-lead-", pending: flagsQuery.isPending || flagsQuery.fetchStatus !== "idle" });
  const { data: me } = useMe();
  const viewedIds = useViewedFlagIds(me?.id);
  const excludedMatchesQuery = useExcludedReuseMatches(id, { includeReferenceList, includeBlockQuote });
  const viewer = viewerQuery.data;
  const sourceAvailability: SourceAvailability = viewer?.available === false
    ? viewer.purged_at ? "deleted" : "unavailable" : undefined;
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const singlePaneLayout = useMediaQuery(DOCUMENT_SINGLE_PANE_MEDIA_QUERY, false);
  const docxScrollInPane = isDocx && singlePaneLayout;
  const paragraphsQuery = useManuscriptParagraphs(id, isDocx);
  const anchorRegion = parsedAnchorRegion && viewer?.available && (
    parsedAnchorRegion.page !== null
      ? viewer.source_format === "pdf" && (viewer.page_count === null || parsedAnchorRegion.page <= viewer.page_count)
      : viewer.source_format === "docx" && [
        ...(paragraphsQuery.data?.paragraphs ?? []),
        ...(paragraphsQuery.data?.tables ?? []),
        ...(paragraphsQuery.data?.images ?? []),
        ...(paragraphsQuery.data?.native_math?.containers ?? []),
      ].some((item) => item.paragraph === parsedAnchorRegion.paragraph)
  ) ? parsedAnchorRegion : null;
  const anchorSourceLoaded = !viewerQuery.isPending && (!isDocx || !paragraphsQuery.isPending);
  const findings = useMemo(() => buildReviewFindings(flags ?? []), [flags]);
  const otherFindings = useMemo(() => findings.filter((finding) => !isOptionalComparisonLead(finding.representative)), [findings]);
  const optional = useMemo(() => comparisonLeadCollection(id, {
    data: flags, error: flagsQuery.error, fetchStatus: flagsQuery.fetchStatus, isError: flagsQuery.isError, isSuccess: flagsQuery.isSuccess,
  }), [id, flags, flagsQuery.error, flagsQuery.fetchStatus, flagsQuery.isError, flagsQuery.isSuccess]);
  const numbersByFlagId = useMemo(() => findingNumberByFlagId(findings), [findings]);
  const selectedFinding = isDecision ? null : findingForFlag(findings, requestedFlagId);
  const selectedComparison = Boolean(selectedFinding && isOptionalComparisonLead(selectedFinding.representative));
  const fullWidthReading = searchParams.get("reading") === "full" && selectedComparison
    && optional.snapshot?.ordinalById.has(requestedFlagId ?? 0) === true && !isExploring && !isDecision && !recordedAnchor;
  const leadFilter = comparisonLeadFilter(searchParams.get("lead_view"));
  const rawLocation = searchParams.get("location");
  const parsedLocation = rawLocation ? Number(rawLocation) : null;
  const selectedLocationId = selectedFinding
    ? selectedFinding.flags.some((flag) => flag.id === parsedLocation)
      ? parsedLocation!
      : requestedFlagId && selectedFinding.flags.some((flag) => flag.id === requestedFlagId)
        ? requestedFlagId
        : selectedFinding.representative.id
    : null;
  const invalidSelection = requestedFlagId !== null && flagsQuery.isSuccess && flagsQuery.fetchStatus === "idle" && !selectedFinding;
  const filteredFindings = otherFindings.filter((finding) => filter === "all" || (filter === "resolved" ? finding.isResolved : !finding.isResolved));
  const filteredComparisons = findings.filter((finding) => isOptionalComparisonLead(finding.representative)
    && (leadFilter === "all" || (leadFilter === "resolved" ? finding.isResolved : !finding.isResolved)))
    .sort((left, right) => left.representative.id - right.representative.id);
  const navigationFindings = selectedComparison ? filteredComparisons
    : selectedFinding && filteredFindings.some((finding) => finding.key === selectedFinding.key) ? filteredFindings : otherFindings;
  const selectedNavigationIndex = selectedFinding ? navigationFindings.findIndex((finding) => finding.key === selectedFinding.key) : -1;
  const previousFinding = selectedNavigationIndex > 0 ? navigationFindings[selectedNavigationIndex - 1] : null;
  const nextFinding = selectedNavigationIndex >= 0 && selectedNavigationIndex < navigationFindings.length - 1 ? navigationFindings[selectedNavigationIndex + 1] : null;
  useEffect(() => () => { leadContinuation.current?.ownership.dispose(); }, []);
  useLayoutEffect(() => {
    const request = leadContinuation.current;
    if (!request) return;
    const search = searchParams.toString();
    if (request.run !== id || (search !== request.search && search !== request.from)) {
      request.ownership.dispose(); leadContinuation.current = undefined; return;
    }
    if (search !== request.search) return;
    leadContinuation.current = undefined;
    const target = document.getElementById(request.targetId)
      ?? (request.targetId === "evidence-heading" ? document.getElementById("selected-finding-heading") : null)
      ?? document.getElementById(COMPARISON_LEAD_HEADING_ID);
    if (request.ownership.owns()) sourceReveal.adopt(target);
    const focused = request.ownership.focus(target, request.targetId === "evidence-heading" ? { preventScroll: true } : undefined);
    if (focused && target?.id === "evidence-heading" && inspectorRef.current) {
      scrollReviewEvidence(target, inspectorRef.current);
    }
  });

  function update(change: (next: URLSearchParams) => void, replace = false) {
    const next = new URLSearchParams(searchParams);
    change(next);
    if (["finding", "flag", "location", "anchor", "match"].some((key) => next.get(key) !== searchParams.get(key))) clearPdfReading(next);
    setSearchParams(next, { replace });
  }

  function taskParameters(change?: (next: URLSearchParams) => void) {
    clearRouteReturnFocus(`signal-saved-source-${id}-`);
    clearRouteReturnFocus(`signal-resolution-source-${id}-`);
    if (sourcePlacement.current) sourcePlacement.current.allowed = false;
    returnSourceFocus.current = undefined;
    criterionInspectionRun.current = null;
    setCriterionSource(undefined);
    leadContinuation.current?.ownership.dispose();
    leadContinuation.current = undefined;
    const next = new URLSearchParams(searchParams);
    next.delete(SAVED_CRITERION_PARAM);
    clearPdfReading(next);
    change?.(next);
    return next;
  }

  function updateTask(change: (next: URLSearchParams) => void, replace = false) {
    setSearchParams(taskParameters(change), { replace });
  }

  function criterionSourceTarget(anchor: string) {
    const next = new URLSearchParams(searchParams);
    clearPdfReading(next);
    next.set("anchor", anchor);
    next.set("pane", "manuscript");
    return `/report/${id}/document?${next}`;
  }

  function openCriterionSource(anchor: string, actionId: string) {
    const action = document.getElementById(actionId);
    const owned = action?.isConnected === true && document.activeElement === action && document.hasFocus();
    const target = new URL(criterionSourceTarget(anchor), window.location.origin);
    const destination = target.search.slice(1);
    const originSearch = criterionSource?.run === id && sameSourceTask(criterionSource.destination, searchParams.toString())
      ? criterionSource.origin : searchParams.toString();
    const originParams = new URLSearchParams(originSearch);
    originParams.set("pane", "review");
    const savedAction = actionId.match(/^signal-saved-source-(\d+)-(\d+)-\d+$/);
    const record = savedAction && Number(savedAction[1]) === id
      ? reportQuery.data?.results.find((row) => row.criterion_id === Number(savedAction[2])) : undefined;
    const recordCriterionId = record?.criterion_id;
    if (recordCriterionId !== undefined) originParams.set(SAVED_CRITERION_PARAM, String(recordCriterionId));
    const origin = originParams.toString();
    if (owned) {
      rememberRouteReturnFocus(`/report/${id}/document?${searchParams}`, `/report/${id}/document?${destination}`, actionId,
        { fallbackElementId: recordCriterionId === undefined ? "escalated-heading" : criterionRecordId(id, recordCriterionId) });
    }
    criterionInspectionRun.current = id;
    setCriterionSource({ attempt: ++sourceAttemptSequence.current, run: id, anchor, origin, from: searchParams.toString(), destination, actionId, recordCriterionId, recordIsInstructor: Boolean(record?.resolution) });
    if (owned) {
      sourcePanelRef.current?.focus();
      if (document.activeElement !== sourcePanelRef.current) manuscriptTabRef.current?.focus();
    }
    sourceFocusOwner.current = owned && (document.activeElement === sourcePanelRef.current || document.activeElement === manuscriptTabRef.current)
      ? document.activeElement as HTMLElement : null;
    sourcePlacement.current = { run: id, origin: searchParams.toString(), destination, adopted: false, allowed: sourceFocusOwner.current !== null };
    setSearchParams(new URLSearchParams(destination), { state: location.state });
  }

  const sourceQuery = searchParams.toString();
  liveSourceScope.current = { run: id, query: sourceQuery };
  useEffect(() => {
    criterionInspectionRun.current = null;
    if (sourcePlacement.current) sourcePlacement.current.allowed = false;
    returnSourceFocus.current = undefined;
    sourceFocusOwner.current = null;
  }, [id]);
  useEffect(() => {
    const ownTransition = criterionSource && sameSourceTask(sourceQuery, criterionSource.from) && !sourcePlacement.current?.adopted;
    if (criterionSource && (criterionSource.run !== id || (!ownTransition && !sameSourceTask(sourceQuery, criterionSource.destination) && !sameSourceTask(sourceQuery, criterionSource.origin)))) setCriterionSource(undefined);
  }, [id, sourceQuery, criterionSource]);
  useEffect(() => {
    const attempt = sourcePlacement.current;
    if (!attempt || !attempt.allowed) return;
    if (attempt.run !== id || (attempt.destination !== sourceQuery && (attempt.adopted || attempt.origin !== sourceQuery))) {
      attempt.allowed = false;
      return;
    }
    if (attempt.destination === sourceQuery) attempt.adopted = true;
    const retire = () => { attempt.allowed = false; returnSourceFocus.current = undefined; };
    const focus = (event: FocusEvent) => { if (event.target !== sourceFocusOwner.current) retire(); };
    window.addEventListener("blur", retire);
    window.addEventListener("keydown", retire);
    window.addEventListener("pointerdown", retire);
    window.addEventListener("wheel", retire, { passive: true });
    document.addEventListener("focusin", focus);
    return () => {
      window.removeEventListener("blur", retire);
      window.removeEventListener("keydown", retire);
      window.removeEventListener("pointerdown", retire);
      window.removeEventListener("wheel", retire);
      document.removeEventListener("focusin", focus);
    };
  }, [id, sourceQuery, criterionSource?.attempt]);

  useEffect(() => () => { if (sourcePlacement.current) sourcePlacement.current.allowed = false; }, []);

  const placeCriterionSource = useCallback((element: HTMLElement) => {
    const attempt = sourcePlacement.current;
    if (!attempt?.allowed || attempt.run !== liveSourceScope.current.run || attempt.destination !== liveSourceScope.current.query || !document.hasFocus() || document.activeElement !== sourceFocusOwner.current) return;
    attempt.adopted = true;
    attempt.allowed = false;
    element.scrollIntoView({ block: "nearest" });
  }, []);

  function returnToCriterion(event: React.MouseEvent<HTMLButtonElement>) {
    if (!criterionSource || criterionSource.run !== id) return;
    const owned = event.currentTarget.isConnected && document.activeElement === event.currentTarget && document.hasFocus();
    if (sourcePlacement.current) sourcePlacement.current.allowed = false;
    if (owned) {
      inspectorRef.current?.focus();
      if (document.activeElement !== inspectorRef.current) reviewTabRef.current?.focus();
      returnSourceFocus.current = { from: sourceQuery, search: criterionSource.origin, actionId: criterionSource.actionId, owner: document.activeElement, recordCriterionId: criterionSource.recordCriterionId };
    }
    setSearchParams(new URLSearchParams(criterionSource.origin), { state: location.state });
    setCriterionSource(undefined);
  }

  useLayoutEffect(() => {
    const requested = returnSourceFocus.current;
    if (!requested) return;
    if (requested.search !== sourceQuery) {
      if (requested.from !== sourceQuery) returnSourceFocus.current = undefined;
      return;
    }
    returnSourceFocus.current = undefined;
    if (!document.hasFocus() || document.activeElement !== requested.owner) return;
    const action = document.getElementById(requested.actionId);
    if (requested.recordCriterionId !== undefined) {
      const target = action ?? document.getElementById(criterionRecordId(id, requested.recordCriterionId)) ?? document.getElementById("criteria-results-heading");
      if (target) { revealCriterionRecord(target); target.focus(); }
      return;
    }
    const resultId = requested.actionId.split("-").at(-2);
    (action ?? document.getElementById(`signal-resolution-heading-${resultId}`) ?? document.getElementById("escalated-heading"))?.focus();
  });

  function selectFinding(flagId: number, replace = false) {
    const finding = findingForFlag(findings, flagId);
    if (!finding) return;
    queueScrollRef.current = inspectorRef.current?.scrollTop ?? 0;
    queueFocusRef.current = finding.representative.id;
    if (isOptionalComparisonLead(finding.representative)) {
      const next = taskParameters();
      next.set("view", "finding"); next.set("finding", String(flagId)); next.set("location", String(flagId)); next.set("pane", "review");
      next.delete("lead_return"); next.delete("lead_visible");
      const position = filteredComparisons.findIndex((item) => item.key === finding.key);
      if (position >= 0) next.set("lead_page", String(Math.floor(position / COMPARISON_LEAD_PAGE_SIZE) + 1));
      for (const key of ["flag", "anchor", "panel", "match"]) next.delete(key);
      ownLeadContinuation(next, "selected-finding-heading");
      sourceReveal.arm(next.toString(), flagId);
      setSearchParams(next, { replace });
      return;
    }
    updateTask((next) => {
      next.set("view", "finding");
      clearPdfReading(next);
      next.set("finding", String(finding.representative.id));
      next.set("location", String(flagId));
      next.delete("flag");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
      next.delete("pane");
    }, replace);
  }

  function ownLeadContinuation(next: URLSearchParams, targetId: string) {
    leadContinuation.current?.ownership.dispose();
    leadContinuation.current = undefined;
    if (!document.hasFocus()) return;
    const host = singlePaneLayout && activeTab === "manuscript" ? headingRef.current : inspectorRef.current;
    host?.focus({ preventScroll: true });
    leadContinuation.current = { run: id, from: searchParams.toString(), search: next.toString(), targetId, ownership: claimFocusContinuation() };
  }

  function showQueue() {
    if (selectedComparison && selectedFinding) {
      restoreQueueRef.current = false;
      const next = taskParameters();
      next.set("view", "queue"); next.set("pane", "review");
      for (const key of ["finding", "flag", "location", "anchor", "panel", "match", "reading"]) next.delete(key);
      const position = filteredComparisons.findIndex((finding) => finding.key === selectedFinding.key);
      if (position >= 0) {
        next.set("lead_page", String(Math.floor(position / COMPARISON_LEAD_PAGE_SIZE) + 1));
        next.delete("lead_return");
      } else {
        next.set("lead_return", String(selectedFinding.representative.id));
      }
      next.delete("lead_visible");
      ownLeadContinuation(next, position >= 0 ? `comparison-lead-${selectedFinding.representative.id}` : COMPARISON_LEAD_HEADING_ID);
      setSearchParams(next);
      return;
    }
    restoreQueueRef.current = true;
    updateTask((next) => {
      next.set("view", "queue");
      next.set("pane", "review");
      next.delete("finding");
      next.delete("flag");
      next.delete("location");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
    });
  }

  useEffect(() => {
    if (!restoreQueueRef.current || selectedFinding || isDecision || isExploring || flagsQuery.isPending) return;
    restoreQueueRef.current = false;
    requestAnimationFrame(() => {
      if (inspectorRef.current) inspectorRef.current.scrollTop = queueScrollRef.current;
      if (queueFocusRef.current !== null) document.getElementById(`review-finding-${queueFocusRef.current}`)?.focus();
    });
  }, [selectedFinding, isDecision, isExploring, flagsQuery.isPending]);

  function openExplore() {
    updateTask((next) => {
      next.set("view", "matches");
      next.set("pane", "review");
      next.delete("finding");
      next.delete("flag");
      next.delete("location");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
    });
  }

  function openDecision(target: "criteria" | "readiness") {
    const next = taskParameters((next) => {
      next.set("view", "decision");
      next.set("pane", "review");
      next.delete("finding");
      next.delete("flag");
      next.delete("location");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
    });
    ownLeadContinuation(next, target === "criteria" ? "escalated-heading" : "analysis-heading");
    setSearchParams(next);
  }

  function setFilter(value: ReviewFilter) {
    updateTask((next) => {
      if (value === "open") next.delete("filter");
      else next.set("filter", value);
      next.set("view", "queue");
      next.set("pane", "review");
      for (const key of ["finding", "flag", "location", "anchor", "panel", "match"]) next.delete(key);
    }, true);
  }

  function setToggle(key: "ref" | "quote", value: boolean) {
    update((next) => { if (value) next.set(key, "1"); else next.delete(key); });
  }

  function setMatch(value: string | null) {
    updateTask((next) => { if (value) { next.set("match", value); next.delete("pane"); } else next.delete("match"); });
  }

  function selectPane(pane: "manuscript" | "review", moveFocus = false) {
    update((next) => { if (pane === "review") next.set("pane", "review"); else next.delete("pane"); }, true);
    if (moveFocus) (pane === "manuscript" ? manuscriptTabRef : reviewTabRef).current?.focus();
  }

  function showSelectedReview() {
    const next = new URLSearchParams(searchParams);
    next.set("pane", "review");
    ownLeadContinuation(next, "selected-finding-heading");
    setSearchParams(next, { replace: true });
  }

  function changeReading(expanded: boolean) {
    const next = new URLSearchParams(searchParams);
    if (expanded) next.set("reading", "full"); else next.delete("reading");
    next.set("pane", "review");
    ownLeadContinuation(next, expanded ? "evidence-heading" : singlePaneLayout ? "selected-finding-heading" : `comparison-reading-expand-${selectedLocationId}`);
    setSearchParams(next);
  }

  function revealRecordedLocation() {
    if (selectedLocationId === null) return;
    sourceReveal.arm(searchParams.toString(), selectedLocationId);
    setSourceRevealAttempt((attempt) => attempt + 1);
  }

  function tabKey(event: KeyboardEvent<HTMLButtonElement>) {
    let next: "manuscript" | "review" | null = null;
    if (event.key === "ArrowLeft" || event.key === "Home") next = "manuscript";
    if (event.key === "ArrowRight" || event.key === "End") next = "review";
    if (!next) return;
    event.preventDefault();
    selectPane(next, true);
  }

  const expandedMatch = excludedMatchesQuery.data?.matches.find((match) => match.id === expandedMatchId) ?? null;
  const selectedRegion = recordedAnchor ? anchorRegion : isExploring
    ? expandedMatch?.own_region ?? null
    : selectedLocationId
      ? viewer?.regions.find((region) => region.flag_id === selectedLocationId) ?? null
      : anchorRegion;
  const requestedPage = selectedRegion?.page ?? null;
  const browseRegions = representativeRegions(findings, viewer?.regions ?? []);
  const visibleRegions = recordedAnchor || isDecision
    ? []
    : isExploring && expandedMatch
      ? [...browseRegions, expandedMatch.own_region]
      : selectedFinding && selectedRegion
        ? [selectedRegion]
        : browseRegions;
  const selectedRegionId = recordedAnchor ? null : selectedRegion?.flag_id ?? null;
  const sourcePhysicallyVisible = !fullWidthReading && (!singlePaneLayout || activeTab === "manuscript");
  const sourceReveal = useSelectedSourceReveal({ runId: id, search: searchParams.toString(),
    requestedId: !isExploring && !isDecision && !recordedAnchor ? requestedFlagId : null,
    resolvedId: viewer?.available && viewer.source_format === "docx" ? selectedRegionId : null,
    visible: sourcePhysicallyVisible, source: sourcePanelRef });
  const selectedSummary = selectedFinding?.flags.find((flag) => flag.id === selectedLocationId) ?? selectedFinding?.representative ?? null;
  const selectedPrecision = selectedFinding ? regionPrecision(selectedRegion, sourceAvailability) : null;
  const reviewContext = ["Review.", reportQuery.data && `${reportQuery.data.pending_review_count} criteria awaiting judgment.`,
    optional.snapshot && `${optional.lastLoaded ? "Last loaded: " : ""}${optional.snapshot.total} optional comparison leads.`,
    flags && otherFindings.length > 0 && `${optional.state !== "success" ? "Last loaded: " : ""}${otherFindings.filter((finding) => !finding.isResolved).length} other open findings.`]
    .filter(Boolean).join(" ");

  return (
    <div className="signal-route signal-page-flow signal-document-viewer">
      <header className="signal-route-header signal-document-header">
        <div><nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Manuscript review</span></nav><h1 ref={headingRef} tabIndex={-1}>Manuscript review</h1>{viewer?.original_filename && <p className="signal-route-header__intro">{viewer.original_filename}</p>}</div>
        {validId && <ActionLink to={`/report/${id}`} variant="secondary">Open full readiness report</ActionLink>}
      </header>

      {!validId ? <Alert title="This manuscript address is invalid" tone="error" role="alert"><ReviewDeskReturnLink variant="secondary">Return to Review Desk</ReviewDeskReturnLink></Alert>
        : (
          <section className="signal-document-workbench" aria-label="Manuscript and review" data-reading={fullWidthReading ? "full" : undefined} data-source-available={viewer?.available === false ? "false" : "true"}>
            <div role="tablist" aria-label="Manuscript review view" className="signal-document-tabs">
              <button ref={manuscriptTabRef} id="manuscript-tab" type="button" role="tab" aria-controls="manuscript-panel" aria-selected={activeTab === "manuscript"} tabIndex={activeTab === "manuscript" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("manuscript")}>Manuscript</button>
              <button ref={reviewTabRef} id="review-tab" type="button" role="tab" aria-controls="review-panel" aria-label={reviewContext} aria-selected={activeTab === "review"} tabIndex={activeTab === "review" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("review")}>Review</button>
            </div>
            <div className="signal-document-grid">
              <div ref={sourcePanelRef} tabIndex={docxScrollInPane ? 0 : -1} id="manuscript-panel" role="tabpanel" aria-labelledby="manuscript-tab" data-active={activeTab === "manuscript"} data-source-format={viewer?.source_format} className="signal-document-pane">
                {viewer?.available && viewer.source_format !== "pdf" && searchParams.get("source") === "page_text" && <p className="signal-document-reconstruction-note" role="status">Page text is available only for PDF sources. Showing the available source view.</p>}
                {reportOrigin && <ActionLink to={`${reportOrigin.returnPath}#${reportOrigin.elementId}`} state={reportOrigin.state} variant="secondary">{reportQuery.data?.results.find((row) => row.criterion_id === reportOrigin.criterionId)?.resolution ? "Return to saved judgment" : "Return to criterion record"}</ActionLink>}
                {criterionSource?.run === id && isDecision && sameSourceTask(sourceQuery, criterionSource.destination) && <Button variant="secondary" onClick={returnToCriterion}>{criterionSource.recordCriterionId === undefined ? "Return to criterion review" : criterionSource.recordIsInstructor ? "Return to saved judgment" : "Return to criterion record"}</Button>}
                {selectedFinding && selectedSummary && selectedPrecision && (
                  <div className="signal-document-selection-strip" role="status"><div><strong>{selectedComparison ? `${comparisonLeadIdentity(selectedSummary, optional.snapshot).marker} · Record #${selectedSummary.id}` : `Finding ${selectedFinding.number} selected`}</strong><span>{instructorAnchor(recordedAnchor ?? selectedSummary.page_anchor)} · {selectedComparison ? "Comparison lead, not scored" : selectedPrecision.label}</span></div><div><Button variant="secondary" onClick={showSelectedReview}>View review</Button>{selectedRegion?.kind === "paragraph_only" && <Button variant="quiet" onClick={revealRecordedLocation}>Show recorded location</Button>}<Button variant="quiet" onClick={showQueue}>Clear selection</Button></div></div>
                )}
                {recordedAnchor && anchorRegion && <div className="signal-document-reconstruction-note" role="status"><strong>Source location: {instructorAnchor(recordedAnchor)}</strong><p>This opens the recorded page or extracted paragraph, not an exact passage highlight. Extracted paragraphs are source positions, not printed Word page numbers.</p></div>}
                {recordedAnchor && anchorSourceLoaded && !anchorRegion && <Alert title="The recorded criterion anchor could not be placed" tone="warning">Review the recorded excerpt in Review and inspect the manuscript manually.</Alert>}
                {rawSelection && requestedFlagId === null && <Alert title="The selected finding address is invalid" tone="warning">Showing the review queue instead.</Alert>}
                {viewerQuery.isPending ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>Loading stored source</span></div></div><div className="signal-document-scroll"><section role="status" aria-live="polite" aria-busy="true" className="signal-document-pdf-skeleton"><span>Loading full manuscript.</span><i /><i /><i /></section></div></div>
                  : viewerQuery.isError || !viewer ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>Stored source</span></div></div><div className="signal-document-scroll"><section className="signal-document-paper-state"><Alert title="The manuscript could not be loaded" tone="error" role="alert"><p>VERIDICAL could not load the stored source view. The Review pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={() => viewerQuery.refetch()}>Try source again</Button><ReviewDeskReturnLink variant="quiet">Return to Review Desk</ReviewDeskReturnLink></Alert></section></div></div>
                    : !viewer.available ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>{viewer.original_filename ?? "Stored source"}</span></div></div><div className="signal-document-scroll"><section className="signal-document-unavailable"><p className="signal-section-kicker">Stored source unavailable</p><h2>Source manuscript unavailable</h2><p>{viewer.purged_at ? `The stored source file was deleted on ${localDate(viewer.purged_at)} and can no longer be opened.` : viewer.unavailable_reason}</p><p>You can still review the recorded evidence and readiness details, but VERIDICAL cannot show the full manuscript.</p><ActionLink to={`/report/${id}`} variant="brand">Open full readiness report</ActionLink></section></div></div>
                      : viewer.source_format === "pdf" ? <PdfSourceReader key={`${me?.id ?? "unknown"}:${id}`} run={id} manuscript={viewer.manuscript_id} actor={me?.id} visible={sourcePhysicallyVisible} fileUrl={`${BASE_URL}/check-runs/${id}/document/file`} regions={visibleRegions} flags={flags ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFinding} requestedPage={requestedPage} findingNumbers={numbersByFlagId} comparisonLeads={optional.snapshot} focusSelection={activeTab === "manuscript" && !fullWidthReading} />
                        : viewer.source_format === "docx" ? <DocxPane key={id} paragraphs={paragraphsQuery.data?.paragraphs} tables={paragraphsQuery.data?.tables} images={paragraphsQuery.data?.images} omittedImageOccurrences={paragraphsQuery.data?.omitted_image_occurrences} nativeMath={paragraphsQuery.data?.native_math} imageBasePath={`/check-runs/${id}/document/images`} paragraphsPending={paragraphsQuery.isPending} paragraphsError={paragraphsQuery.isError} onRetry={() => paragraphsQuery.refetch()} regions={visibleRegions} flags={flags ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFinding} isVisible={sourcePhysicallyVisible} focusSelection={activeTab === "manuscript" && !fullWidthReading} findingNumbers={numbersByFlagId} comparisonLeads={optional.snapshot} requestedParagraph={anchorRegion?.paragraph ?? null} onPlaceRequestedParagraph={criterionInspectionRun.current === id && isDecision ? placeCriterionSource : undefined} requestedParagraphAttempt={criterionSource?.attempt} scrollInParent={docxScrollInPane} />
                          : <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>{viewer.original_filename ?? "Unsupported source"}</span></div></div><div className="signal-document-scroll"><section className="signal-document-paper-state"><Alert title="This manuscript format cannot be displayed here" tone="warning"><p>Use the recorded excerpts and anchors in Review, or open the full readiness report.</p><ActionLink to={`/report/${id}`} variant="secondary">Open full readiness report</ActionLink></Alert></section></div></div>}
              </div>
              <aside ref={inspectorRef} tabIndex={-1} id="review-panel" role="tabpanel" aria-labelledby="review-tab" data-active={activeTab === "review"} className="signal-document-inspector">
                {isExploring ? <ReuseExplorePanel flags={flags} flagsFetching={flagsQuery.isFetching} flagsError={flagsQuery.isError} flagsSucceeded={flagsQuery.isSuccess} onRetryFlags={() => flagsQuery.refetch()} onSelectFlag={selectFinding} onBack={showQueue} includeReferenceList={includeReferenceList} includeBlockQuote={includeBlockQuote} onToggleReferenceList={(value) => setToggle("ref", value)} onToggleBlockQuote={(value) => setToggle("quote", value)} excludedMatchesQuery={excludedMatchesQuery} expandedMatchId={expandedMatchId} onExpandMatch={setMatch} />
                  : isDecision ? <ReadinessDecision checkRunId={id} report={reportQuery.data} pending={reportQuery.isPending} error={reportQuery.error} retrying={reportQuery.fetchStatus === "fetching"} onRetry={() => reportQuery.refetch()} onBack={showQueue} sourceTarget={criterionSourceTarget} onOpenSource={openCriterionSource} />
                    : selectedFinding && selectedLocationId ? <SelectedFinding fullWidth={fullWidthReading} singlePane={singlePaneLayout} onReadingChange={changeReading} finding={selectedFinding} total={otherFindings.length} locationFlagId={selectedLocationId} region={selectedRegion} sourceAvailability={sourceAvailability} sourceAnchor={recordedAnchor} previous={previousFinding} next={nextFinding} collection={optional} outsideFilter={selectedComparison && selectedNavigationIndex < 0 && optional.snapshot !== undefined} filterPosition={selectedNavigationIndex + 1} filterTotal={navigationFindings.length} onAll={() => update((next) => { next.delete("lead_view"); next.delete("lead_visible"); next.delete("lead_return"); next.set("lead_page", String(Math.floor((optional.snapshot?.records.findIndex((record) => record.id === selectedLocationId) ?? 0) / COMPARISON_LEAD_PAGE_SIZE) + 1)); }, true)} onBack={showQueue} onPrevious={() => previousFinding && selectFinding(previousFinding.representative.id, true)} onNext={() => nextFinding && selectFinding(nextFinding.representative.id, true)} onLocation={selectFinding} onExplore={openExplore} />
                      : <ReviewHome checkRunId={id} report={reportQuery.data} reportPending={reportQuery.isPending} reportRetrying={reportQuery.fetchStatus === "fetching"} reportError={reportQuery.error} onReportRetry={() => reportQuery.refetch()} findings={otherFindings} regions={viewer?.regions ?? []} sourceAvailability={sourceAvailability} collection={optional} viewedIds={viewedIds} onFlagsRetry={() => { void flagsQuery.refetch(); }} filter={filter} invalidSelection={invalidSelection} onFilter={setFilter} onSelect={selectFinding} onExplore={openExplore} onDecision={openDecision} />}
              </aside>
            </div>
          </section>
        )}
    </div>
  );
}
