import { useEscalatedItems } from "../report/useReport";
import { criterionSourceReturn } from "../report/criterionSourceReturn";
import { ManuscriptSourcePane } from "./ManuscriptSourcePane";
import { FindingModeDisclosure, FlagReviewActions } from "../flags/FlagReviewActions";
import { clearPdfReading, sameSourceTask } from "./usePdfPageText";
import { useQueryClient } from "@tanstack/react-query";
import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
import { type KeyboardEvent, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigationType, useParams, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { FlagOut, FlagRegionOut, ReportOut } from "../api/types";
import { CRITERION_ANCHOR_REGION_ID, COMPARISON_LEAD_PAGE_SIZE, LIST_PAGE_SIZE } from "../config/ui";
import { checkKindMeta, findingVerificationPrompt, isPValueFinding } from "../domain/checkKind";
import { agreementEvidenceType, evidenceDisplayState } from "../domain/evidenceDisplay";
import { DECISION_LABEL } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { problemLabel } from "../domain/problemLabel";
import { severityLabel } from "../domain/severity";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { instructorAnchor } from "../domain/sourceAnchor";
import { useMe } from "../auth/useAuth";
import { useViewedFlagIds } from "../domain/flagViewed";
import { COMPARISON_LEAD_COPY, COMPARISON_LEAD_HEADING_ID, comparisonLeadFilter, comparisonLeadIdentity, isOptionalComparisonLead } from "../domain/comparisonLeadIdentity";
import { ComparisonLeadList } from "../report/ComparisonLeadList";
import { comparisonLeadCollection, type ComparisonLeadCollection } from "../report/comparisonLeadCollection";
import { IntegrityModuleNavigation, IntegrityModuleSummary, selectedIntegrityModule } from "../report/IntegrityModules";
import { StatisticalCheckScope } from "../report/StatisticalCheckScope";
import { OriginalityScope } from "../report/OriginalityScope";
import { useFlag } from "../flags/useFlag";
import { PValueComparison } from "../flags/PValueComparison";
import { AgreementComparison, type AgreementStatementRole } from "../flags/AgreementComparison";
import { CitationComparison } from "../flags/CitationComparison";
import { CitationSourceLinks } from "../flags/CitationSourceLinks";
import { CitationHistoryWarning } from "../flags/CitationHistoryWarning";
import { findingMethodLabel, isCitationMetadataFinding } from "../domain/findingMethod";
import { RuleScopeSummary } from "../report/RuleScopeNotice";
import { unconfirmedAutomaticRuleRows } from "../domain/ruleScope";
import { ReportReadNotice } from "../report/ReportReadNotice";
import { ReportAssessmentContext } from "../report/ReportAssessmentContext";
import { reportAssessmentNotices } from "../domain/reportAssessmentContext";
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
import { clearRouteReturnFocus, pendingRouteReturnElement, rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { criterionRecordId, revealCriterionRecord, SAVED_CRITERION_PARAM } from "../report/savedJudgment";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { clampPage, Pagination, pageOf } from "../ui/Pagination";
import { moduleEmptyState } from "../report/moduleEmptyState";
import { useReviewWorkspaceCapacity } from "./useReviewWorkspaceCapacity";
import { ReadinessBand } from "../ui/ReadinessBand";
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
import { RecordedEvidenceReader } from "./RecordedEvidenceReader";
import { FindingSourceComparison } from "./FindingSourceComparison";
import type { EvidenceSourceRequest, OpenEvidenceSource } from "../report/evidenceTypes";
import { RecordedFindingSources } from "../report/RecordedEvidencePage";
import { WORKSPACE_STEP_PARAM, workspaceStep, type ReportStep } from "../report/reviewStep";
import { WorkspaceStepFooter, WorkspaceStepHeading, WorkspaceStepNavigation } from "./WorkspaceSteps";
import { InWorkspaceBar, LeadingChevronIcon, WorkspaceBarContext } from "./WorkspaceBar";

type ReviewFilter = "open" | "resolved" | "all";
const FINDINGS_PAGE_PARAM = "findings_page";

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

function ReviewQueue({ findings, regions, sourceAvailability, report, collection, viewedIds, filter, invalidSelection, onFilter, onSelect, onExplore, onRetry, showComparisons = true, moduleKind }: {
  moduleKind: string;
  findings: ReviewFinding[];
  regions: FlagRegionOut[];
  sourceAvailability?: SourceAvailability;
  report: ReportOut;
  collection: ComparisonLeadCollection;
  viewedIds: ReadonlySet<number>;
  onRetry: () => void;
  showComparisons?: boolean;
  filter: ReviewFilter;
  invalidSelection: boolean;
  onFilter: (filter: ReviewFilter) => void;
  onSelect: (id: number) => void;
  onExplore: () => void;
}) {
  const open = findings.filter((finding) => !finding.isResolved);
  const resolved = findings.filter((finding) => finding.isResolved);
  const visible = filter === "open" ? open : filter === "resolved" ? resolved : findings;
  const [params, setParams] = useSearchParams();
  const page = clampPage(params.get(FINDINGS_PAGE_PARAM), visible.length, LIST_PAGE_SIZE.findings);
  const pageItems = pageOf(visible, page, LIST_PAGE_SIZE.findings);
  function selectPage(next: number) {
    const updated = new URLSearchParams(params);
    if (next === 1) updated.delete(FINDINGS_PAGE_PARAM); else updated.set(FINDINGS_PAGE_PARAM, String(next));
    setParams(updated, { replace: true });
    requestAnimationFrame(() => document.getElementById("workspace-findings-list")?.focus());
  }

  return (
    <div className="signal-document-review-queue">
      {invalidSelection && <Alert title="That finding is no longer available" tone="warning" role="status">The review queue is open.</Alert>}
      {findings.length === 0 && !showComparisons && collection.state === "success" && (() => {
        const empty = moduleEmptyState(report, moduleKind);
        return <EmptyState id="workspace-module-empty" title={empty.title} action={empty.limits
          ? <ActionLink to={`/report/${report.check_run_id}?section=limits`} variant="secondary">Open assessment limits</ActionLink> : undefined}><p>{empty.description}</p></EmptyState>;
      })()}
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
        <Alert title={collection.state !== "success" ? "Last loaded: no open integrity findings" : "No open integrity findings"} tone="info"><p>{collection.state !== "success" ? "Every other finding in the last loaded records has an instructor resolution. Current records have not been confirmed." : "Every recorded integrity finding has an instructor resolution. You can review resolved findings or continue to the recorded grades."}</p><Button variant="secondary" onClick={() => onFilter("resolved")}>Show resolved findings</Button></Alert>
      ) : (
        <><ol id="workspace-findings-list" tabIndex={-1} aria-label="Findings on this page" className="signal-document-review-list">
          {pageItems.map((finding) => {
            const precision = regionPrecision(regionForFinding(finding, regions), sourceAvailability);
            const state = finding.isSourceConfirmed ? "Source confirmed" : finding.isResolved ? "Resolved" : finding.isConfirmed ? "Confirmed" : "Open";
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
        <Pagination label="Finding pages" page={page} pageSize={LIST_PAGE_SIZE.findings} total={visible.length} onPage={selectPage} /></>
      ))}
      {showComparisons && <ComparisonLeadList collection={collection} workspace={findings.length === 0 && report.pending_review_count === 0} headingLevel={2} showActions viewedIds={viewedIds} onSelect={onSelect} onRetry={onRetry} />}
      {showComparisons && <Button variant="quiet" onClick={onExplore}>Other passage matches not included in readiness</Button>}
    </div>
  );
}

function SelectedFinding({ comparisonHost, comparisonVisible, finding, total, locationFlagId, region, sourceAvailability, sourceAnchor, previous, next, collection, outsideFilter, filterPosition, filterTotal, onAll, onPrevious, onNext, onLocation, onExplore, fullWidth, singlePane, onReadingChange, onRecordReady, onStatementSource, activeStatement, onRevealSource }: {
  comparisonHost?: HTMLElement | null;
  comparisonVisible?: boolean;
  finding: ReviewFinding; total: number; locationFlagId: number; region: FlagRegionOut | null;
  sourceAvailability?: SourceAvailability; sourceAnchor: string | null;
  previous: ReviewFinding | null; next: ReviewFinding | null;
  collection: ComparisonLeadCollection; outsideFilter: boolean; filterPosition: number; filterTotal: number;
  onAll: () => void; onBack: () => void; onPrevious: () => void; onNext: () => void;
  onLocation: (flagId: number) => void; onExplore: () => void;
  fullWidth: boolean; singlePane: boolean; onReadingChange: (expanded: boolean) => void;
  onRevealSource?: () => void;
  onRecordReady: (record: FlagOut | undefined, settled: boolean) => void; onStatementSource: (anchor: string | null, role: AgreementStatementRole) => void; activeStatement?: AgreementStatementRole;
}) {
  const query = useFlag(locationFlagId);
  const client = useQueryClient();
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const detail = query.data?.id === locationFlagId && query.data.check_run_id === Number(checkRunId)
    && recoverableReadError(query.error) ? query.data : undefined;
  const pairReady = detail && ((detail.check_kind === "internal_agreement" && detail.agreement_pair)
    || (detail.check_kind === "originality_reuse" && detail.passage_pair)) ? detail.id : null;
  useLayoutEffect(() => { onRecordReady(detail, !query.isPending); }, [detail, query.isPending, onRecordReady]);
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
    <p role="status" aria-live="polite" className="sr-only">{fullWidth ? "Original pages are shown beside the comparison." : ""}</p>
    {fullWidth && <div ref={readingBarRef} className="signal-comparison-reading-bar" data-source-presented={Boolean(comparisonHost && comparisonVisible)}><div><strong>{optional ? identity.marker : `Finding ${finding.number}`} · Record #{locationFlagId}</strong><span>{instructorAnchor(sourceAnchor ?? finding.representative.page_anchor)}</span></div><Button id="comparison-reading-return" variant="secondary" onClick={() => onReadingChange(false)}>Return to manuscript</Button></div>}
    <header className="signal-document-selected__header">
      <p className="signal-section-kicker">{optional ? `${collection.lastLoaded ? "Last loaded: " : ""}${identity.label}` : `Finding ${finding.number} of ${total}`}</p>
      <h2 id="selected-finding-heading" ref={headingRef} tabIndex={-1}>{findingTitle(finding)}</h2>
      <div className="signal-document-selected__states"><span className={optional ? "signal-comparison-status" : `signal-severity signal-severity--${finding.severity}`}>{optional ? COMPARISON_LEAD_COPY.badge : severityLabel(finding.severity)}</span><span className="signal-resolution-state">{finding.isResolved ? "Resolved" : "Open"}</span></div>
      {onRevealSource && <Button variant="quiet" onClick={onRevealSource}>Show recorded location</Button>}
      {optional && outsideFilter && <><p>{COMPARISON_LEAD_COPY.outsideFilter}</p><Button variant="quiet" onClick={onAll}>Show all comparison leads</Button></>}
      {!optional && filterPosition > 0 && <p className="signal-field-hint">{filterPosition} of {filterTotal} in {checkKindMeta(finding.representative.check_kind).eyebrow.replace(/ check$/, "")}</p>}
      {optional && qualified && !outsideFilter && filterTotal !== collection.snapshot?.total && filterPosition > 0 && <p>Item {filterPosition} of {filterTotal} in this filter.</p>}
      {(optional ? navigationWasShown.current : previous || next) && <InWorkspaceBar><div className="signal-document-finding-nav" role="group" aria-label={optional ? "Move between comparison leads" : "Move between review findings"}>
        <Button variant="secondary" disabled={optional ? undefined : !canPrevious} aria-disabled={optional ? !canPrevious : undefined} onClick={() => move(canPrevious, onPrevious)}>{optional ? "Previous comparison lead" : "Previous finding"}</Button>
        <Button variant="secondary" disabled={optional ? undefined : !canNext} aria-disabled={optional ? !canNext : undefined} onClick={() => move(canNext, onNext)}>{optional ? "Next comparison lead" : "Next finding"}</Button>
      </div></InWorkspaceBar>}
    </header>
    {optional && collection.lastLoaded && <div role="status" className="signal-field-hint">{collection.state === "error" ? "The comparison list could not be refreshed. Showing last loaded records." : collection.state === "paused" ? "Waiting for a connection. Showing last loaded comparison records." : "Updating comparison records. Showing last loaded records."}{collection.state === "error" && <Button variant="quiet" onClick={retryComparisonList}>Retry comparison list</Button>}</div>}
    {detail && query.isError && <Alert title="These details could not be refreshed" tone="warning"><p>Showing the last loaded record. Check its source before drawing a conclusion.</p><Button variant="quiet" onClick={() => { void query.refetch(); }}>Try again</Button></Alert>}
    {query.isPending && !detail ? <div role="status" aria-live="polite" aria-busy={query.fetchStatus === "fetching"} className="signal-desk-loading"><span>{query.fetchStatus === "paused" ? "Waiting to load" : "Loading"} {optional ? "comparison lead record" : `Finding ${finding.number}`}.</span><i /><i /></div>
      : !detail ? <Alert title={optional ? "Comparison lead details could not be loaded" : "Finding details could not be loaded"} tone="error" role="alert"><p>The manuscript and review queue are still available. Try loading this record again.</p><Button variant="secondary" onClick={() => { if (document.hasFocus()) headingRef.current?.focus({ preventScroll: true }); void query.refetch(); }}>Try again</Button></Alert>
        : <SelectedFindingEvidence comparisonHost={comparisonHost} comparisonVisible={comparisonVisible} finding={finding} flag={detail} current={!query.isError && query.fetchStatus === "idle"} locationFlagId={locationFlagId} region={region} sourceAvailability={sourceAvailability} sourceAnchor={sourceAnchor} onLocation={onLocation} onExplore={onExplore} fullWidth={fullWidth} canExpand={pairReady !== null} onReadManuscript={() => onReadingChange(false)} onExpand={() => onReadingChange(true)} onStatementSource={onStatementSource} activeStatement={activeStatement} showPageSources={singlePane} />}
  </article>;
}

function SelectedFindingEvidence({ comparisonHost, comparisonVisible, finding, flag, current, locationFlagId, region, sourceAvailability, sourceAnchor, onLocation, onExplore, fullWidth, canExpand, onExpand, onReadManuscript, onStatementSource, activeStatement, showPageSources }: {
  showPageSources: boolean;
  comparisonHost?: HTMLElement | null;
  comparisonVisible?: boolean;
  finding: ReviewFinding; flag: FlagOut; locationFlagId: number; region: FlagRegionOut | null;
  current: boolean;
  sourceAvailability?: SourceAvailability; sourceAnchor: string | null;
  onLocation: (flagId: number) => void; onExplore: () => void;
  fullWidth: boolean; canExpand: boolean; onReadManuscript: () => void; onExpand: () => void; onStatementSource: (anchor: string | null, role: AgreementStatementRole) => void; activeStatement?: AgreementStatementRole;
}) {
  const { showsManuscriptQuote, reasoningText, showsManuscriptEvidence, isResubmission } = evidenceDisplayState(flag);
  const precision = regionPrecision(region, sourceAvailability);
  const comparative = flag.check_kind === "originality_reuse";
  const optional = isOptionalComparisonLead(flag);
  const wholeComparisonWithoutPair = optional && !flag.is_passage_level && !flag.passage_pair && !flag.evidence_unavailable;
  const kindMeta = checkKindMeta(flag.check_kind);
  const verificationPrompt = findingVerificationPrompt(flag.check_kind, flag.ai_verdict_summary, !!flag.passage_pair, !!flag.citation_metadata_comparison);
  const pValueFinding = flag.check_kind === "statistical_forensics" && isPValueFinding(flag.ai_verdict_summary);
  const citationMetadata = isCitationMetadataFinding(flag);
  const locationIndex = finding.flags.findIndex((member) => member.id === locationFlagId);
  const recordedSummary = !showsManuscriptQuote ? systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence") : null;
  const recordedReasoning = reasoningText ? systemFindingCopy(reasoningText, flag.ai_verdict_summary, "reasoning") : null;

  return <>

      {wholeComparisonWithoutPair
        ? <p className="signal-document-precision-note"><strong>Whole-manuscript comparison.</strong> No passage pair was saved.</p>
        : <p className="signal-field-hint">{instructorAnchor(sourceAnchor ?? flag.page_anchor)} · {precision.label}.</p>}
      {finding.flags.length > 1 && (
        <section className="signal-document-locations" aria-labelledby="finding-locations-heading">
          <h3 id="finding-locations-heading">Recorded locations and scopes</h3>
          <p>Location {locationIndex + 1} of {finding.flags.length}. These records are presented as one finding; none of the stored data was removed.</p>
          <div role="group" aria-label={`Locations for Finding ${finding.number}`}>{finding.flags.map((member, index) => <button key={member.id} type="button" aria-pressed={member.id === locationFlagId} onClick={() => onLocation(member.id)}>Location {index + 1}: {member.cited_text ? `${member.cited_text}, ${instructorAnchor(member.page_anchor)}` : instructorAnchor(member.page_anchor)}</button>)}</div>
        </section>
      )}
      {!showsManuscriptQuote && <p><strong>Recorded check result:</strong> {systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}</p>}
      <CitationHistoryWarning flag={flag} />
      {!wholeComparisonWithoutPair && <section aria-labelledby="evidence-heading" className="signal-document-detail-section">
        <h3 id="evidence-heading" tabIndex={-1}>Evidence to compare or check</h3>
        {canExpand && !fullWidth && <Button id={`comparison-reading-expand-${locationFlagId}`} variant="secondary" onClick={onExpand}>{flag.agreement_pair ? "Compare statements" : "Compare passages"}</Button>}
        {fullWidth && canExpand && <FindingSourceComparison flag={flag} current={current} onSource={onStatementSource} pageHost={comparisonHost} sourceVisible={comparisonVisible} onReadManuscript={onReadManuscript} />}
        {flag.passage_pair && !(fullWidth && canExpand) && <PassagePairPanel pair={flag.passage_pair} ownAnchor={instructorAnchor(flag.page_anchor)} wholeManuscript={!flag.is_passage_level} variant="flag" placement="inspector" />}
        {!flag.passage_pair && showsManuscriptQuote && flag.check_kind !== "internal_agreement" && <blockquote><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{instructorAnchor(flag.page_anchor)}</cite></blockquote>}
        <PValueComparison flag={flag} />
        {!(fullWidth && canExpand) && <AgreementComparison flag={flag} findingNumber={finding.number} onSource={onStatementSource} activeRole={activeStatement} />}
        <CitationComparison flag={flag} />
        <CitationSourceLinks flag={flag} />
        {showPageSources && !(fullWidth && canExpand) && <RecordedFindingSources flag={flag} current={current} />}
        {flag.ai_verdict_summary === "orphan_in_text_citation" ? <p className="signal-field-hint">The saved check did not link this citation to a reference-list entry. A matching entry is not available for side-by-side comparison.</p> : !citationMetadata && !comparative && !pValueFinding && flag.check_kind !== "internal_agreement" && <p className="signal-field-hint">This check evaluates one manuscript location. A second passage does not apply.</p>}
      </section>}
      {!wholeComparisonWithoutPair && <p className="signal-document-precision-note"><strong>{precision.label}</strong>. {precision.helper} Recorded location: <span>{instructorAnchor(sourceAnchor ?? flag.page_anchor)}</span>.</p>}
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
      {!optional && !pValueFinding && (recordedReasoning || flag.cited_text) && <section aria-labelledby="found-heading" className="signal-document-detail-section">
        <h3 id="found-heading">What the check found</h3>
        {flag.cited_text && <p><strong>{flag.ai_verdict_summary === "uncited_reference" ? "Reference entry" : "Citation"}:</strong> {flag.cited_text}</p>}
        {recordedReasoning && <p>{recordedReasoning}</p>}
        <p className="signal-field-hint">This is the system's explanation. You decide whether it holds.</p>
      </section>}
      {!wholeComparisonWithoutPair && <section aria-labelledby="verify-heading" className="signal-document-detail-section signal-document-detail-section--verify"><h3 id="verify-heading">What the instructor should verify</h3><p>{verificationPrompt}</p></section>}
      {optional && (!pValueFinding || flag.p_value_comparison) && <details className="signal-document-recorded-reasoning"><summary>{flag.citation_metadata_review_warning ? "Original recorded explanation and technical details" : "Recorded reasoning and technical details"}</summary>
        {optional && <dl className="signal-document-finding-facts">
          <div><dt>Check</dt><dd>{kindMeta.eyebrow}</dd></div>
          <div><dt>Check method</dt><dd>{findingMethodLabel(flag)}</dd></div>
          <div><dt>Evidence type</dt><dd>{flag.passage_pair ? "Recorded passage comparison" : "Comparison without a stored pair"}</dd></div>
          <div><dt>Recorded location</dt><dd>{(sourceAnchor ?? flag.page_anchor).trim() ? instructorAnchor(sourceAnchor ?? flag.page_anchor) : "No location was recorded."}</dd></div>
        </dl>}
        <p>This is the system's explanation. You decide whether it holds.</p>{optional && recordedReasoning && recordedReasoning === recordedSummary ? <p>The recorded explanation is shown in the check result above.</p> : recordedReasoning ? <p>{recordedReasoning}</p> : <p>No additional recorded reasoning is available.</p>}</details>}
      {!optional && <details className="signal-document-recorded-reasoning"><summary>Source and check details</summary><dl className="signal-document-finding-facts">
        <div><dt>Record</dt><dd>#{flag.id}</dd></div>
        <div><dt>Check</dt><dd>{kindMeta.eyebrow}</dd></div>
        <div><dt>Check method</dt><dd>{findingMethodLabel(flag)}</dd></div>
        <div><dt>Evidence type</dt><dd>{citationMetadata ? flag.citation_metadata_comparison ? "Recorded registry metadata comparison" : "Registry comparison without saved provider metadata" : flag.check_kind === "internal_agreement" ? agreementEvidenceType(flag) : comparative ? flag.passage_pair ? "Recorded passage comparison" : "Comparison without a stored pair" : "Single-location check"}</dd></div>
      </dl></details>}
      <FlagReviewActions key={`${flag.check_run_id}:${flag.id}`} flag={flag} />
  </>;
}

function ReportUnavailable({ checkRunId, error, onRetry }: { checkRunId: number; error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.code === "conflict") return <Alert title="This review is not ready yet" tone="info" role="alert"><p>The check has not produced a complete readiness record.</p><ActionLink to={`/checks/${checkRunId}`} variant="secondary">View check progress</ActionLink></Alert>;
  return <Alert title="Could not load review details" tone="error" role="alert"><p>VERIDICAL could not load the recorded review. The source pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={onRetry}>Try review again</Button><ReviewDeskReturnLink variant="quiet">Return to Review Desk</ReviewDeskReturnLink></Alert>;
}

// Each step lands on its own heading so the new content is announced and in
// view; the findings list has no fixed heading, so its step button is used.
const STEP_FOCUS_TARGET: Record<ReportStep, string> = {
  criteria: "escalated-heading", findings: "workspace-step-findings",
  grades: "criteria-results-heading", decision: "analysis-heading",
};

function ReadinessDecision({ checkRunId, step, report: loadedReport, pending, error, retrying, onRetry, sourceTarget, onOpenSource, onOpenEvidenceSource, returnNotice }: { checkRunId: number; step: Exclude<ReportStep, "findings"> | null; report: ReportOut | undefined; pending: boolean; retrying: boolean; error: unknown; onRetry: () => void; sourceTarget: CriterionSourceTarget; onOpenSource: CriterionSourceOpen; onOpenEvidenceSource: OpenEvidenceSource; returnNotice?: string }) {
  const [params] = useSearchParams();
  const selectedCriterion = params.has("criterion");
  const report = usableReport(loadedReport, checkRunId, error);
  const readFailed = Boolean(report && error);
  if ((pending && !report) || (report && !step)) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading the review.</span><i /><i /></div>;
  if (!report) return <ReportUnavailable checkRunId={checkRunId} error={error} onRetry={onRetry} />;

  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  const coverage = coverageItems(report);
  const criterionRecords = report.results;
  return (
    <div className="signal-document-analysis">
      {readFailed && <ReportReadNotice retrying={retrying} onRetry={onRetry} />}
      {step && <WorkspaceStepHeading step={step} />}
      {/* The criteria panel carries these notices itself; grades and the
          decision must show the same AI-mode and evidence caveats. */}
      {(step === "grades" || (step === "decision" && !selectedCriterion)) && <ReportAssessmentContext notices={reportAssessmentNotices(report, "manuscript")} />}
      {step === "criteria" && <>
        {returnNotice && <p role="status" className="signal-document-precision-note">{returnNotice}</p>}
        <SignalEscalatedPanel checkRunId={report.check_run_id} sourceTarget={sourceTarget} onOpenSource={onOpenSource} onOpenEvidenceSource={onOpenEvidenceSource} />
      </>}
      {step === "grades" && <>
        {returnNotice && <p role="status" className="signal-document-precision-note">{returnNotice}</p>}
        <SignalCriteriaResults results={criterionRecords} checkRunId={report.check_run_id} sourceTarget={sourceTarget} onOpenSource={onOpenSource} />
      </>}
      {step === "decision" && !selectedCriterion && <>
      <section className="signal-document-analysis__summary" aria-labelledby="analysis-heading" aria-describedby={readFailed ? "report-read-failure" : undefined}>
        <div className="signal-document-inspector__heading"><h2 id="analysis-heading" tabIndex={-1}>Readiness</h2><p>VERIDICAL's readiness band comes from the recorded grades and findings. You record the final decision below.</p></div>
        <div className="signal-document-analysis__readiness">
          <div><span>{readFailed ? "Last loaded system readiness" : unconfirmedAutomaticRuleRows(report.results).length ? "Saved readiness band" : "System readiness"}</span><ReadinessBand status={report.status} /></div>
          <p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity findings."}</p>
          <RuleScopeSummary rows={report.results} checkRunId={report.check_run_id} />
          <p className="signal-field-hint">{identity.primary} was checked against {report.rubric_title}.</p>
        </div>
      </section>
      <section className="signal-document-decision-summary" aria-labelledby="decision-summary-heading">
        <h3 id="decision-summary-heading">Before you decide</h3>
        <dl className="signal-decision-summary-facts">
          <div><dt>Criteria needing your judgment</dt><dd>{report.pending_review_count}</dd></div>
          <div><dt>Open high-severity findings</dt><dd>{report.unresolved_high_flag_count}</dd></div>
          <div><dt>Assessment limits</dt><dd>{coverage.length}</dd></div>
          <div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div>
        </dl>
        <p className="signal-field-hint">The full record, its assessment limits and the decision are on the readiness report. You record or reopen the decision there.</p>
        <ActionLink to={`/report/${report.check_run_id}#final-decision`} variant="primary">{report.decision ? "Open the recorded decision" : "Record the decision on the readiness report"}</ActionLink>
      </section>
      </>}
    </div>
  );
}

function ReviewHome({ moduleKind, showComparisons = true, checkRunId, report, reportPending, reportRetrying, reportError, onReportRetry, findings, regions, sourceAvailability, collection, viewedIds, onFlagsRetry, filter, invalidSelection, onFilter, onSelect, onExplore }: {
  showComparisons?: boolean;
  moduleKind: string;
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
}) {
  if (reportPending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading review queue.</span><i /><i /></div>;
  const availableReport = usableReport(report, checkRunId, reportError);
  if (!availableReport) return <ReportUnavailable checkRunId={checkRunId} error={reportError} onRetry={onReportRetry} />;
  return <>{Boolean(reportError) && <ReportReadNotice retrying={reportRetrying} onRetry={onReportRetry} />}<ReviewQueue moduleKind={moduleKind} showComparisons={showComparisons} findings={findings} regions={regions} sourceAvailability={sourceAvailability} report={availableReport} collection={collection} viewedIds={viewedIds} onRetry={onFlagsRetry} filter={filter} invalidSelection={invalidSelection} onFilter={onFilter} onSelect={onSelect} onExplore={onExplore} /></>;
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
  const includeReferenceList = searchParams.get("ref") === "1";
  const includeBlockQuote = searchParams.get("quote") === "1";
  const expandedMatchId = searchParams.get("match");
  const activeTab = searchParams.get("pane") === "review" || searchParams.get("pane") === "analysis" ? "review" : "manuscript";
  const filter = parseFilter(searchParams.get("filter"));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const manuscriptTabRef = useRef<HTMLButtonElement>(null);
  const sourcePanelRef = useRef<HTMLDivElement>(null);
  const [comparisonHost, setComparisonHost] = useState<HTMLDivElement | null>(null);
  const [workspaceBar, setWorkspaceBar] = useState<HTMLDivElement | null>(null);
  const sourceFocusOwner = useRef<HTMLElement | null>(null);
  const reviewTabRef = useRef<HTMLButtonElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const capacityFocus = useRef<{ run: number; actor: number | undefined; search: string; pane: "manuscript" | "review"; ownership: FocusContinuation } | undefined>(undefined);
  const queueScrollRef = useRef(0);
  const queueFocusRef = useRef<number | null>(null);
  const restoreQueueRef = useRef(false);
  const leadContinuation = useRef<{ run: number; from: string; search: string; targetId: string; ownership: FocusContinuation } | undefined>(undefined);
  const sourceAttemptSequence = useRef(0);
  const [, setSourceRevealAttempt] = useState(0);
  const criterionInspectionRun = useRef<number | null>(null);
  const [criterionSource, setCriterionSource] = useState<{ attempt: number; run: number; anchor: string; origin: string; from: string; destination: string; actionId: string; ownedReturn: boolean; recordCriterionId?: number; recordIsInstructor?: boolean }>();
  const [recordedSource, setRecordedSource] = useState<{ run: number; actor: number; revision: number; request: EvidenceSourceRequest; attempt: number }>();
  const [criterionReturnNotice, setCriterionReturnNotice] = useState<{ run: number; search: string; message: string }>();
  const sourcePlacement = useRef<{ run: number; origin: string; destination: string; adopted: boolean; allowed: boolean } | undefined>(undefined);
  const liveSourceScope = useRef({ run: id, query: searchParams.toString() });
  const returnSourceFocus = useRef<{ from: string; search: string; actionId: string; ownership: FocusContinuation; recordCriterionId?: number } | undefined>(undefined);
  const retireCriterionReturn = useCallback(() => {
    returnSourceFocus.current?.ownership.dispose();
    returnSourceFocus.current = undefined;
  }, []);
  useEffect(() => () => retireCriterionReturn(), [retireCriterionReturn]);
  const reportOrigin = useMemo(() => criterionSourceReturn(location.state, id), [location.state, id]);

  const viewerQuery = useManuscriptViewer(id);
  const reportQuery = useReport(id);
  const pendingReviewQuery = useEscalatedItems(id);
  const pendingOriginRows = reportOrigin?.kind === "pending" && pendingReviewQuery.isSuccess && !pendingReviewQuery.isError && pendingReviewQuery.fetchStatus === "idle"
    ? pendingReviewQuery.data.filter(row => row.check_result_id === reportOrigin.checkResultId) : [];
  const pendingOriginText = pendingOriginRows.length === 1 ? pendingOriginRows[0].criterion_text : undefined;
  const flagsQuery = useFlags(id);
  const flags = recoverableReadError(flagsQuery.error) ? flagsQuery.data : undefined;
  const pendingReturnId = pendingRouteReturnElement(location.pathname + location.search);
  const returningToPendingCriterion = pendingReturnId?.startsWith("signal-resolution-");
  const localSourceReturn = useMemo(() => criterionSource?.run === id && criterionSource.ownedReturn ? {
    returnPath: `/report/${id}/document?${criterionSource.from}`,
    elementId: criterionSource.actionId,
    fallbackElementId: criterionSource.recordCriterionId === undefined ? "escalated-heading" : criterionRecordId(id, criterionSource.recordCriterionId),
    retainOnSearchChange: true,
  } : undefined, [criterionSource, id]);
  useRouteFocus("Manuscript review - VERIDICAL", headingRef, reportOrigin ?? localSourceReturn, returningToPendingCriterion
    ? { prefix: "signal-resolution-", pending: pendingReviewQuery.isPending || pendingReviewQuery.fetchStatus !== "idle" || pendingReviewQuery.isError }
    : { prefix: "comparison-lead-", pending: flagsQuery.isPending || flagsQuery.fetchStatus !== "idle" });
  const { data: me } = useMe();
  const viewedIds = useViewedFlagIds(me?.id);
  const excludedMatchesQuery = useExcludedReuseMatches(id, { includeReferenceList, includeBlockQuote });
  const viewer = viewerQuery.data;
  const sourceAvailability: SourceAvailability = viewer?.available === false
    ? viewer.purged_at ? "deleted" : "unavailable" : undefined;
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const workspace = useReviewWorkspaceCapacity(active => {
    if (sourcePanelRef.current?.contains(active)) selectPane("manuscript");
    else if (inspectorRef.current?.contains(active)) selectPane("review");
  }, active => {
    const pane = active === manuscriptTabRef.current ? "manuscript" : active === reviewTabRef.current ? "review" : null;
    if (!pane || !document.hasFocus()) return;
    capacityFocus.current?.ownership.dispose();
    headingRef.current?.focus({ preventScroll: true });
    capacityFocus.current = { run: id, actor: me?.id, search: location.search, pane, ownership: claimFocusContinuation() };
  });
  useLayoutEffect(() => {
    const pending = capacityFocus.current;
    if (!pending) return;
    capacityFocus.current = undefined;
    if (!workspace.split || pending.run !== id || pending.actor !== me?.id || pending.search !== location.search) {
      pending.ownership.dispose(); return;
    }
    pending.ownership.focus(pending.pane === "manuscript" ? sourcePanelRef.current : inspectorRef.current, { preventScroll: true });
  }, [workspace.split, id, me?.id, location.search]);
  useEffect(() => () => capacityFocus.current?.ownership.dispose(), []);
  const singlePaneLayout = !workspace.split;
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
  const stepReport = usableReport(reportQuery.data, id, reportQuery.error);
  // Confirmed findings still count for readiness but no longer wait for review.
  const openFindingCount = flags ? otherFindings.filter((finding) => !finding.isResolved && !finding.isConfirmed).length : undefined;
  // Wait for the findings before choosing a default step only when no
  // criterion is waiting, because only then can the findings change it.
  const step = workspaceStep(searchParams, !stepReport ? undefined
    : stepReport.pending_review_count > 0 ? { pendingCriteria: stepReport.pending_review_count, openFindings: 0 }
    : flagsQuery.isPending ? undefined
    : { pendingCriteria: 0, openFindings: openFindingCount ?? stepReport.unresolved_high_flag_count });
  const isDecision = !isExploring && step !== "findings";
  const navigationType = useNavigationType();
  const previousStep = useRef(step);
  // Back/Forward between steps would otherwise leave focus on <body>; only
  // claim it when nothing else on the page holds focus.
  useEffect(() => {
    const changed = previousStep.current !== null && step !== null && previousStep.current !== step;
    previousStep.current = step;
    if (!changed || navigationType !== "POP" || !step) return;
    if (document.activeElement && document.activeElement !== document.body) return;
    requestAnimationFrame(() => document.getElementById(STEP_FOCUS_TARGET[step])?.focus());
  }, [step, navigationType]);
  const selectedFinding = isDecision ? null : findingForFlag(findings, requestedFlagId);
  const selectedModule = selectedIntegrityModule(selectedFinding?.representative.check_kind ?? searchParams.get("module") ?? findings[0]?.representative.check_kind ?? null);
  const moduleFindings = otherFindings.filter(finding => finding.representative.check_kind === selectedModule);
  const selectedComparison = Boolean(selectedFinding && isOptionalComparisonLead(selectedFinding.representative));

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
  const [selectedDetail, setSelectedDetail] = useState<{ run: number; id: number; record: FlagOut | undefined; settled: boolean }>();
  const selectedRecord = selectedDetail?.run === id && selectedDetail.id === selectedLocationId ? selectedDetail.record : undefined;
  const setSelectedRecord = useCallback((record: FlagOut | undefined, settled: boolean) => {
    if (selectedLocationId !== null) setSelectedDetail({ run: id, id: selectedLocationId, record, settled });
  }, [id, selectedLocationId]);
  const pairedFlag = selectedRecord && selectedRecord.check_run_id === id && ((selectedRecord.check_kind === "internal_agreement" && selectedRecord.agreement_pair) || (selectedRecord.check_kind === "originality_reuse" && selectedRecord.passage_pair)) ? selectedRecord.id : null;
  const [statementTarget, setStatementTarget] = useState<{ run: number; flag: number; role: AgreementStatementRole }>();
  const fullWidthReading = searchParams.get("reading") === "full" && pairedFlag === selectedLocationId
    && selectedLocationId !== null && !isExploring && !isDecision;
  const invalidSelection = requestedFlagId !== null && flagsQuery.isSuccess && flagsQuery.fetchStatus === "idle" && !selectedFinding;
  const filteredFindings = moduleFindings.filter((finding) => filter === "all" || (filter === "resolved" ? finding.isResolved : !finding.isResolved));
  const filteredComparisons = findings.filter((finding) => isOptionalComparisonLead(finding.representative)
    && (leadFilter === "all" || (leadFilter === "resolved" ? finding.isResolved : !finding.isResolved)))
    .sort((left, right) => left.representative.id - right.representative.id);
  const navigationFindings = selectedComparison ? filteredComparisons
    : selectedFinding && filteredFindings.some((finding) => finding.key === selectedFinding.key) ? filteredFindings : moduleFindings;
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
    clearRouteReturnFocus(`signal-resolution-coverage-${id}-`);
    if (sourcePlacement.current) sourcePlacement.current.allowed = false;
    retireCriterionReturn();
    criterionInspectionRun.current = null;
    setCriterionSource(undefined);
    leadContinuation.current?.ownership.dispose();
    leadContinuation.current = undefined;
    const next = new URLSearchParams(searchParams);
    next.delete(SAVED_CRITERION_PARAM);
    next.delete("criterion");
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
    setCriterionReturnNotice(undefined);
    setRecordedSource(undefined);
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
    setCriterionSource({ attempt: ++sourceAttemptSequence.current, run: id, anchor, origin, from: searchParams.toString(), destination, actionId, ownedReturn: owned, recordCriterionId, recordIsInstructor: Boolean(record?.resolution) });
    if (owned) {
      sourcePanelRef.current?.focus();
      if (document.activeElement !== sourcePanelRef.current) manuscriptTabRef.current?.focus();
    }
    sourceFocusOwner.current = owned && (document.activeElement === sourcePanelRef.current || document.activeElement === manuscriptTabRef.current)
      ? document.activeElement as HTMLElement : null;
    sourcePlacement.current = { run: id, origin: searchParams.toString(), destination, adopted: false, allowed: sourceFocusOwner.current !== null };
    setSearchParams(new URLSearchParams(destination), { state: reportOrigin ? reportOrigin.state : location.state });
  }

  function openRecordedCriterionSource(request: EvidenceSourceRequest) {
    const revision = reportQuery.data?.assessment_revision;
    if (!me || typeof revision !== "number" || reportQuery.isError || reportQuery.fetchStatus !== "idle"
      || request.reference.criterion_id !== Number(searchParams.get("criterion"))) return;
    openCriterionSource(request.reference.anchor ?? "", request.actionId);
    setRecordedSource({ run: id, actor: me.id, revision, request, attempt: sourceAttemptSequence.current });
  }

  const sourceQuery = searchParams.toString();
  liveSourceScope.current = { run: id, query: sourceQuery };
  useEffect(() => {
    criterionInspectionRun.current = null;
    if (sourcePlacement.current) sourcePlacement.current.allowed = false;
    retireCriterionReturn();
    sourceFocusOwner.current = null;
  }, [id, retireCriterionReturn]);
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
    const retire = () => { attempt.allowed = false; retireCriterionReturn(); };
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
  }, [id, sourceQuery, criterionSource?.attempt, retireCriterionReturn]);

  useEffect(() => () => { if (sourcePlacement.current) sourcePlacement.current.allowed = false; }, []);

  const canPlaceRecordedSource = useCallback(() => {
    const attempt = sourcePlacement.current;
    return Boolean(attempt?.allowed && attempt.run === liveSourceScope.current.run
      && attempt.destination === liveSourceScope.current.query && document.hasFocus()
      && document.activeElement === sourceFocusOwner.current);
  }, []);
  function rearmRecordedSource(action: HTMLElement) {
    const placement = sourcePlacement.current;
    if (!recordedReaderActive || !recordedSource || !sourcePhysicallyVisible
      || recordedSource.actor !== me?.id || recordedSource.run !== id
      || reportQuery.isError || reportQuery.fetchStatus !== "idle"
      || recordedSource.revision !== reportQuery.data?.assessment_revision
      || recordedSource.request.reference.criterion_id !== Number(searchParams.get("criterion"))
      || !placement || placement.run !== id || placement.destination !== sourceQuery
      || !action.isConnected || document.activeElement !== action || !document.hasFocus()) return;
    // Retry is a new deliberate request. Its button can disappear on success,
    // so transfer ownership to the persistent pane before rearming placement.
    sourcePanelRef.current?.focus();
    if (document.activeElement !== sourcePanelRef.current) return;
    sourceFocusOwner.current = sourcePanelRef.current;
    sourcePlacement.current = { ...placement, allowed: true };
    setCriterionSource((previous) => previous?.run === id
      ? { ...previous, attempt: ++sourceAttemptSequence.current } : previous);
  }
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
    retireCriterionReturn();
    if (owned) {
      inspectorRef.current?.focus();
      if (document.activeElement !== inspectorRef.current) reviewTabRef.current?.focus();
      returnSourceFocus.current = { from: sourceQuery, search: criterionSource.origin, actionId: criterionSource.actionId, ownership: claimFocusContinuation(), recordCriterionId: criterionSource.recordCriterionId };
    }
    setSearchParams(new URLSearchParams(criterionSource.origin), { state: location.state });
    setCriterionSource(undefined);
  }

  useLayoutEffect(() => {
    const requested = returnSourceFocus.current;
    if (!requested) return;
    if (requested.search !== sourceQuery) {
      if (requested.from !== sourceQuery) retireCriterionReturn();
      return;
    }
    if (!requested.ownership.owns()) { retireCriterionReturn(); return; }
    const waiting = requested.recordCriterionId === undefined
      ? pendingReviewQuery.isPending || pendingReviewQuery.fetchStatus !== "idle" || pendingReviewQuery.isError
      : reportQuery.isPending || reportQuery.fetchStatus !== "idle" || reportQuery.isError;
    if (waiting) return;
    returnSourceFocus.current = undefined;
    const action = document.getElementById(requested.actionId);
    if (requested.recordCriterionId !== undefined) {
      const target = action ?? document.getElementById(criterionRecordId(id, requested.recordCriterionId)) ?? document.getElementById("criteria-results-heading");
      if (target) { revealCriterionRecord(target); requested.ownership.focus(target); }
      else requested.ownership.dispose();
      return;
    }
    const resultId = requested.actionId.split("-").at(-2);
    if (!action) setCriterionReturnNotice({ run: id, search: sourceQuery,
      message: "The original source action is no longer available. Review the current criterion below." });
    requested.ownership.focus(action ?? document.getElementById(`signal-resolution-heading-${resultId}`) ?? document.getElementById("escalated-heading"));
  }, [sourceQuery, id, pendingReviewQuery.data, pendingReviewQuery.isPending, pendingReviewQuery.fetchStatus,
    pendingReviewQuery.isError, reportQuery.data, reportQuery.isPending, reportQuery.fetchStatus, reportQuery.isError, retireCriterionReturn]);

  function selectFinding(flagId: number, replace = false) {
    const finding = findingForFlag(findings, flagId);
    if (!finding) return;
    queueScrollRef.current = inspectorRef.current?.scrollTop ?? 0;
    queueFocusRef.current = finding.representative.id;
    if (isOptionalComparisonLead(finding.representative)) {
      const next = taskParameters();
      next.set("module", finding.representative.check_kind);
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
    const next = taskParameters((next) => {
      next.set("module", finding.representative.check_kind);
      next.set("view", "finding");
      clearPdfReading(next);
      next.set("finding", String(finding.representative.id));
      next.set("location", String(flagId));
      next.delete("flag");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
      next.delete("pane");
    });
    ownLeadContinuation(next, singlePaneLayout ? "manuscript-panel" : "selected-finding-heading");
    sourceReveal.arm(next.toString(), flagId);
    setSearchParams(next, { replace });
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
    // Return to the page that holds the finding being left, even after
    // Next/Previous moved past the page the list was on.
    const position = selectedFinding ? filteredFindings.findIndex((finding) => finding.key === selectedFinding.key) : -1;
    updateTask((next) => {
      next.set("view", "queue");
      next.set("pane", "review");
      if (position >= LIST_PAGE_SIZE.findings) next.set(FINDINGS_PAGE_PARAM, String(Math.floor(position / LIST_PAGE_SIZE.findings) + 1));
      else if (position >= 0) next.delete(FINDINGS_PAGE_PARAM);
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

  function goToStep(target: ReportStep) {
    restoreQueueRef.current = false;
    // A new step starts at its own top, not at the previous step's scroll depth.
    if (inspectorRef.current) inspectorRef.current.scrollTop = 0;
    const next = taskParameters((next) => {
      next.set("view", target === "findings" ? "queue" : "decision");
      if (target === "findings") next.delete(WORKSPACE_STEP_PARAM); else next.set(WORKSPACE_STEP_PARAM, target);
      next.set("pane", "review");
      for (const key of ["finding", "flag", "location", "anchor", "panel", "match"]) next.delete(key);
    });
    ownLeadContinuation(next, STEP_FOCUS_TARGET[target]);
    setSearchParams(next);
  }

  function setFilter(value: ReviewFilter) {
    updateTask((next) => {
      if (value === "open") next.delete("filter");
      else next.set("filter", value);
      next.set("view", "queue");
      next.set("pane", "review");
      for (const key of ["finding", "flag", "location", "anchor", "panel", "match", FINDINGS_PAGE_PARAM]) next.delete(key);
    }, true);
  }

  function setToggle(key: "ref" | "quote", value: boolean) {
    update((next) => { if (value) next.set(key, "1"); else next.delete(key); });
  }

  function setMatch(value: string | null) {
    updateTask((next) => { if (value) { next.set("match", value); next.delete("pane"); } else next.delete("match"); });
  }

  function selectPane(pane: "manuscript" | "review") {
    const next = new URLSearchParams(searchParams);
    if (pane === "review") next.set("pane", "review"); else next.delete("pane");
    setSearchParams(next, { replace: true, state: location.state });
  }

  function changeReading(expanded: boolean) {
    const next = new URLSearchParams(searchParams);
    if (expanded) next.set("reading", "full"); else next.delete("reading");
    if (!expanded && singlePaneLayout) next.delete("pane"); else next.set("pane", "review");
    ownLeadContinuation(next, expanded ? "evidence-heading" : singlePaneLayout ? "manuscript-panel" : `comparison-reading-expand-${selectedLocationId}`);
    setSearchParams(next);
  }

  function showStatementSource(anchor: string | null, role: AgreementStatementRole) {
    if (!anchor?.trim() || selectedLocationId === null) return;
    setStatementTarget({ run: id, flag: selectedLocationId, role });
    const next = taskParameters(next => {
      next.set("anchor", anchor);
      next.delete("reading");
      next.delete("pane");
    });
    ownLeadContinuation(next, "manuscript-panel");
    setSearchParams(next, { state: location.state });
  }

  function revealRecordedLocation() {
    if (selectedLocationId === null) return;
    if (singlePaneLayout && activeTab !== "manuscript") {
      const next = new URLSearchParams(searchParams);
      next.delete("pane");
      ownLeadContinuation(next, "manuscript-panel");
      sourceReveal.arm(next.toString(), selectedLocationId);
      setSearchParams(next, { replace: true, state: location.state });
      return;
    }
    sourceReveal.arm(searchParams.toString(), selectedLocationId);
    setSourceRevealAttempt((attempt) => attempt + 1);
  }

  function tabKey(event: KeyboardEvent<HTMLButtonElement>) {
    let next: "manuscript" | "review" | null = null;
    if (event.key === "Home") next = "manuscript";
    if (event.key === "End") next = "review";
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      next = event.currentTarget === manuscriptTabRef.current ? "review" : "manuscript";
    }
    if (!next) return;
    event.preventDefault();
    (next === "manuscript" ? manuscriptTabRef : reviewTabRef).current?.focus();
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
  const recordedReaderActive = isDecision && recordedSource?.run === id && recordedSource.actor === me?.id
    && (!recordedAnchor || recordedAnchor === recordedSource.request.reference.anchor);
  const sourceReveal = useSelectedSourceReveal({ runId: id, search: searchParams.toString(),
    requestedId: !isExploring && !isDecision && !recordedAnchor ? requestedFlagId : null,
    resolvedId: viewer?.available && viewer.source_format === "docx" && selectedDetail?.settled
      && selectedDetail.run === id && selectedDetail.id === selectedLocationId ? selectedRegionId : null,
    visible: sourcePhysicallyVisible, source: sourcePanelRef });
  const modeDisclosureId = useId();
  const modeRecord = selectedRecord?.id === selectedLocationId && selectedRecord.check_run_id === id
    && selectedRecord.llm_mode !== "real" && (!isCitationMetadataFinding(selectedRecord) || selectedRecord.llm_mode === "fake") ? selectedRecord : null;
  const reviewContext = ["Review.", reportQuery.data && `${reportQuery.data.pending_review_count} ${reportQuery.data.pending_review_count === 1 ? "criterion" : "criteria"} awaiting judgment.`,
    optional.snapshot && `${optional.lastLoaded ? "Last loaded: " : ""}${optional.snapshot.total} optional comparison leads.`,
    flags && otherFindings.length > 0 && `${optional.state !== "success" ? "Last loaded: " : ""}${otherFindings.filter((finding) => !finding.isResolved).length} other open findings.`]
    .filter(Boolean).join(" ");

  return (
    <div className="signal-route signal-page-flow signal-document-viewer">
      <header className="signal-route-header signal-document-header">
        <div><nav aria-label="Breadcrumb"><ReviewDeskReturnLink>Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Manuscript review</span></nav><h1 ref={headingRef} tabIndex={-1}>Manuscript review</h1>{viewer?.original_filename && <p className="signal-route-header__intro">{viewer.original_filename}</p>}</div>
        {validId && <ActionLink to={`/report/${id}`} variant="secondary">Open readiness report</ActionLink>}
      </header>

      {!validId ? <Alert title="This manuscript address is invalid" tone="error" role="alert"><ReviewDeskReturnLink variant="secondary">Return to Review Desk</ReviewDeskReturnLink></Alert>
        : (<>
          {modeRecord && <FindingModeDisclosure id={modeDisclosureId} mode={modeRecord.llm_mode} registry={isCitationMetadataFinding(modeRecord)} />}
          <div ref={setWorkspaceBar} className="signal-review-bar">
            {(isExploring || selectedFinding) && <nav aria-label="Workspace return" className="signal-review-bar__nav">
              <Button variant="secondary" className="signal-workspace-back" onClick={showQueue}><LeadingChevronIcon />Back to findings list</Button>
            </nav>}
          </div>
          <WorkspaceBarContext.Provider value={workspaceBar}>
          <section ref={workspace.frame} className="signal-document-workbench" aria-label="Manuscript and review" aria-describedby={modeRecord ? modeDisclosureId : undefined} data-layout={workspace.split ? "split" : "single"} data-reading={fullWidthReading ? "full" : undefined} data-source-available={viewer?.available === false ? "false" : "true"}>
            <span ref={workspace.threshold} className="signal-review-workspace-threshold" aria-hidden="true" />
            <div role="tablist" aria-label="Manuscript review view" className="signal-document-tabs">
              <button ref={manuscriptTabRef} id="manuscript-tab" type="button" role="tab" aria-controls="manuscript-panel" aria-selected={activeTab === "manuscript"} tabIndex={activeTab === "manuscript" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("manuscript")}>Manuscript</button>
              <button ref={reviewTabRef} id="review-tab" type="button" role="tab" aria-controls="review-panel" aria-label={reviewContext} aria-selected={activeTab === "review"} tabIndex={activeTab === "review" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("review")}>Review</button>
            </div>
            <div className="signal-document-grid">
              <div ref={sourcePanelRef} tabIndex={-1} id="manuscript-panel" role="tabpanel" aria-labelledby="manuscript-tab" data-active={activeTab === "manuscript"} data-source-format={viewer?.source_format} className="signal-document-pane">
                <div ref={setComparisonHost} className="signal-source-page-comparison-host" hidden={!fullWidthReading} />
                <div className="signal-document-original-reader" hidden={fullWidthReading}>
                {viewer?.available && viewer.source_format !== "pdf" && searchParams.get("source") === "page_text" && <p className="signal-document-reconstruction-note" role="status">Page text is available only for PDF sources. Showing the available source view.</p>}
                {reportOrigin?.kind === "pending" ? <div className="signal-document-source-context">
                  <p><strong>Source opened from criterion review</strong>{pendingOriginText && <>: {pendingOriginText}</>}</p>
                  <ActionLink to={`${reportOrigin.returnPath}#${reportOrigin.elementId}`} state={reportOrigin.state} variant="secondary" aria-label={pendingOriginText ? `Return to criterion review: ${pendingOriginText}` : undefined}>Return to criterion review</ActionLink>
                </div> : reportOrigin && <ActionLink to={`${reportOrigin.returnPath}#${reportOrigin.elementId}`} state={reportOrigin.state} variant="secondary">{reportQuery.data?.results.find((row) => row.criterion_id === reportOrigin.criterionId)?.resolution ? "Return to saved judgment" : "Return to criterion record"}</ActionLink>}
                {!reportOrigin && criterionSource?.run === id && isDecision && sameSourceTask(sourceQuery, criterionSource.destination) && <Button variant="secondary" onClick={returnToCriterion}>{criterionSource.recordCriterionId === undefined ? "Return to criterion review" : criterionSource.recordIsInstructor ? "Return to saved judgment" : "Return to criterion record"}</Button>}
                {!recordedReaderActive && recordedAnchor && anchorRegion && <div className="signal-document-reconstruction-note" role="status"><strong>Source location: {instructorAnchor(recordedAnchor)}</strong><p>This opens the recorded page or extracted paragraph, not an exact passage highlight. Extracted paragraphs are source positions, not printed Word page numbers.</p></div>}
                {!recordedReaderActive && recordedAnchor && anchorSourceLoaded && !anchorRegion && <Alert title="The recorded criterion anchor could not be placed" tone="warning">Review the recorded excerpt in Review and inspect the manuscript manually.</Alert>}
                {rawSelection && requestedFlagId === null && <Alert title="The selected finding address is invalid" tone="warning">Showing the review queue instead.</Alert>}
                <ManuscriptSourcePane run={id} actor={me?.id} visible={sourcePhysicallyVisible} viewerQuery={viewerQuery} paragraphsQuery={paragraphsQuery}
                  recordedReader={recordedReaderActive && recordedSource ? <RecordedEvidenceReader
                    key={`${id}:${me?.id}`} run={id} request={recordedSource.request} attempt={recordedSource.attempt}
                    current={!reportQuery.isError && reportQuery.fetchStatus === "idle" && recordedSource.revision === reportQuery.data?.assessment_revision
                      && recordedSource.request.reference.criterion_id === Number(searchParams.get("criterion"))}
                    visible={sourcePhysicallyVisible} onPlaced={placeCriterionSource} canPlace={canPlaceRecordedSource} onRetry={rearmRecordedSource} onManual={() => setRecordedSource(undefined)} /> : undefined}
                  regions={visibleRegions} flags={flags ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFinding} requestedPage={requestedPage}
                  findingNumbers={numbersByFlagId} comparisonLeads={optional.snapshot} focusSelection={false}
                  requestedParagraph={anchorRegion?.paragraph ?? null} onPlaceRequestedParagraph={criterionInspectionRun.current === id && isDecision ? placeCriterionSource : undefined}
                  requestedParagraphAttempt={criterionSource?.attempt} scrollInParent={false} />
                </div>
              </div>
              <aside ref={inspectorRef} tabIndex={-1} id="review-panel" role="tabpanel" aria-labelledby="review-tab" data-active={activeTab === "review"} className="signal-document-inspector">
                <WorkspaceStepNavigation current={step} onSelect={goToStep} counts={{
                  pendingCriteria: stepReport?.pending_review_count, openFindings: openFindingCount,
                  recordedGrades: stepReport?.results.filter((row) => row.outcome !== "escalated").length,
                  instructorGrades: stepReport?.results.filter((row) => row.outcome !== "escalated" && row.resolution).length,
                  decision: stepReport ? stepReport.decision : undefined }} />
                {!isExploring && !isDecision && !selectedFinding && <WorkspaceStepHeading step="findings" />}
                {!isExploring && !isDecision && !selectedFinding && <div className="signal-document-module-overview">
                  <IntegrityModuleNavigation selected={selectedModule} controls="workspace-module-records" modules={reportQuery.data?.integrity_modules}
                    counts={new Map(["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"].map(kind => [kind, flags?.filter(flag => flag.check_kind === kind).length ?? 0]))}
                    onSelect={kind => updateTask(next => { next.set("module", kind); ["finding", "flag", "location", "reading", "anchor", "view", "pane", "lead_view", "lead_return", "lead_visible", FINDINGS_PAGE_PARAM].forEach(key => next.delete(key)); next.set("view", "queue"); })} />
                  <IntegrityModuleSummary kind={selectedModule} />
                  {selectedModule === "statistical_forensics" && reportQuery.data && <StatisticalCheckScope scope={reportQuery.data.statistical_check_scope} />}
                  {selectedModule === "originality_reuse" && reportQuery.data && <OriginalityScope scope={reportQuery.data.originality_scope} showAddAction />}
                </div>}
                <div id="workspace-module-records">
                {isExploring ? <ReuseExplorePanel showBack={false} flags={flags} flagsFetching={flagsQuery.isFetching} flagsError={flagsQuery.isError} flagsSucceeded={flagsQuery.isSuccess} onRetryFlags={() => flagsQuery.refetch()} onSelectFlag={selectFinding} onBack={showQueue} includeReferenceList={includeReferenceList} includeBlockQuote={includeBlockQuote} onToggleReferenceList={(value) => setToggle("ref", value)} onToggleBlockQuote={(value) => setToggle("quote", value)} excludedMatchesQuery={excludedMatchesQuery} expandedMatchId={expandedMatchId} onExpandMatch={setMatch} />
                  : isDecision ? <ReadinessDecision checkRunId={id} step={step} report={reportQuery.data} pending={reportQuery.isPending} error={reportQuery.error} retrying={reportQuery.fetchStatus === "fetching"} onRetry={() => reportQuery.refetch()} sourceTarget={criterionSourceTarget} onOpenSource={openCriterionSource} onOpenEvidenceSource={openRecordedCriterionSource}
                    returnNotice={criterionReturnNotice?.run === id && criterionReturnNotice.search === sourceQuery ? criterionReturnNotice.message : undefined} />
                    : selectedFinding && selectedLocationId ? <SelectedFinding comparisonHost={comparisonHost} comparisonVisible={workspace.split || activeTab === "manuscript"} onRevealSource={selectedRegion?.kind === "paragraph_only" ? revealRecordedLocation : undefined} activeStatement={statementTarget?.run === id && statementTarget.flag === selectedLocationId ? statementTarget.role : undefined} onRecordReady={setSelectedRecord} onStatementSource={showStatementSource} fullWidth={fullWidthReading} singlePane={singlePaneLayout} onReadingChange={changeReading} finding={selectedFinding} total={otherFindings.length} locationFlagId={selectedLocationId} region={selectedRegion} sourceAvailability={sourceAvailability} sourceAnchor={recordedAnchor} previous={previousFinding} next={nextFinding} collection={optional} outsideFilter={selectedComparison && selectedNavigationIndex < 0 && optional.snapshot !== undefined} filterPosition={selectedNavigationIndex + 1} filterTotal={navigationFindings.length} onAll={() => update((next) => { next.delete("lead_view"); next.delete("lead_visible"); next.delete("lead_return"); next.set("lead_page", String(Math.floor((optional.snapshot?.records.findIndex((record) => record.id === selectedLocationId) ?? 0) / COMPARISON_LEAD_PAGE_SIZE) + 1)); }, true)} onBack={showQueue} onPrevious={() => previousFinding && selectFinding(previousFinding.representative.id, true)} onNext={() => nextFinding && selectFinding(nextFinding.representative.id, true)} onLocation={selectFinding} onExplore={openExplore} />
                      : <ReviewHome moduleKind={selectedModule} showComparisons={selectedModule === "originality_reuse"} checkRunId={id} report={reportQuery.data} reportPending={reportQuery.isPending} reportRetrying={reportQuery.fetchStatus === "fetching"} reportError={reportQuery.error} onReportRetry={() => reportQuery.refetch()} findings={moduleFindings} regions={viewer?.regions ?? []} sourceAvailability={sourceAvailability} collection={optional} viewedIds={viewedIds} onFlagsRetry={() => { void flagsQuery.refetch(); }} filter={filter} invalidSelection={invalidSelection} onFilter={setFilter} onSelect={selectFinding} onExplore={openExplore} />}
                </div>
                {step && !isExploring && !selectedFinding && !searchParams.has("criterion") && <WorkspaceStepFooter current={step} onSelect={goToStep} />}
              </aside>
            </div>
          </section>
          </WorkspaceBarContext.Provider>
        </>)}
    </div>
  );
}
