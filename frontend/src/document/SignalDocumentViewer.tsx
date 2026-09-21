import { type KeyboardEvent, useEffect, useMemo, useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ApiError, BASE_URL } from "../api/client";
import type { FlagRegionOut, ReportOut } from "../api/types";
import { CRITERION_ANCHOR_REGION_ID } from "../config/ui";
import { checkKindMeta } from "../domain/checkKind";
import { DECISION_LABEL } from "../domain/decisionTone";
import { evidenceDisplayState } from "../domain/evidenceDisplay";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { problemLabel } from "../domain/problemLabel";
import { severityLabel } from "../domain/severity";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { useFlag } from "../flags/useFlag";
import { SignalDecisionPanel } from "../report/SignalDecisionPanel";
import { SignalCriteriaResults, SignalEscalatedPanel } from "../report/SignalReviewSections";
import { coverageItems } from "../report/reportCoverage";
import {
  useExcludedReuseMatches,
  useFlags,
  useManuscriptParagraphs,
  useManuscriptViewer,
  useReport,
} from "../report/useReport";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { CoverageStatement } from "../ui/CoverageStatement";
import { ReadinessBand } from "../ui/ReadinessBand";
import { DocxPane } from "./DocxPane";
import { PassagePairPanel } from "./PassagePairPanel";
import { PdfPane } from "./PdfPane";
import { regionPrecision } from "./regionCopy";
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
  const page = anchor.match(/\b(?:page|p\.)\s*(\d+)\b/i);
  if (page) {
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
  const paragraph = anchor.match(/(?:¶|paragraph\s+)(\d+)/i);
  if (paragraph) {
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

function instructorAnchor(anchor: string): string {
  return /^(?:\u00b6|\u00c2\u00b6|paragraph\s+)\d+$/i.test(anchor.trim())
    ? "Reconstructed text"
    : anchor;
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

function ReviewQueue({ findings, regions, report, pending, error, filter, invalidSelection, onFilter, onSelect, onExplore, onDecision }: {
  findings: ReviewFinding[];
  regions: FlagRegionOut[];
  report: ReportOut;
  pending: boolean;
  error: boolean;
  filter: ReviewFilter;
  invalidSelection: boolean;
  onFilter: (filter: ReviewFilter) => void;
  onSelect: (id: number) => void;
  onExplore: () => void;
  onDecision: () => void;
}) {
  if (pending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading review findings.</span><i /><i /></div>;
  if (error) return <Alert title="Review findings could not be loaded" tone="error" role="alert">VERIDICAL could not load this review queue. Return to the Review Desk and reopen the manuscript to try again.</Alert>;

  const open = findings.filter((finding) => !finding.isResolved);
  const resolved = findings.filter((finding) => finding.isResolved);
  const visible = filter === "open" ? open : filter === "resolved" ? resolved : findings;

  return (
    <div className="signal-document-review-queue">
      <div className="signal-document-inspector__heading">
        <p className="signal-section-kicker">Your review tasks</p>
        <h2>Review queue</h2>
        <p>{findings.length} {findings.length === 1 ? "finding" : "findings"}. {resolved.length} resolved.</p>
      </div>
      {invalidSelection && <Alert title="That finding is no longer available" tone="warning" role="status">The review queue is open.</Alert>}
      {findings.length > 0 && (
        <div className="signal-document-review-filters" role="group" aria-label="Filter review findings">
          {(["open", "resolved", "all"] as const).map((value) => {
            const count = value === "open" ? open.length : value === "resolved" ? resolved.length : findings.length;
            const label = value[0].toUpperCase() + value.slice(1);
            return <button key={value} type="button" aria-pressed={filter === value} onClick={() => onFilter(value)}>{label} {count}</button>;
          })}
        </div>
      )}
      {findings.length === 0 ? (
        <Alert title="No integrity findings were recorded" tone="info">This run recorded no integrity findings. This does not replace criteria review. Continue with any criteria that need your judgment.</Alert>
      ) : visible.length === 0 && filter === "open" ? (
        <Alert title="No open integrity findings" tone="info"><p>Every recorded integrity finding has an instructor resolution. You can review resolved findings or continue to readiness and decision.</p><Button variant="secondary" onClick={() => onFilter("resolved")}>Show resolved findings</Button></Alert>
      ) : (
        <ol className="signal-document-review-list">
          {visible.map((finding) => {
            const precision = regionPrecision(regionForFinding(finding, regions));
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
      )}
      {report.pending_review_count > 0 && (
        <section className="signal-document-criterion-task" aria-labelledby="criterion-task-heading">
          <div><p className="signal-section-kicker">Criterion task</p><h3 id="criterion-task-heading">{report.pending_review_count} {report.pending_review_count === 1 ? "criterion needs" : "criteria need"} your judgment</h3><p>Review these before readiness is settled.</p></div>
          <Button variant="secondary" onClick={onDecision}>Review criteria</Button>
        </section>
      )}
      <Button variant="quiet" onClick={onExplore}>Other passage matches not included in readiness</Button>
      <section className="signal-document-readiness-card" aria-labelledby="readiness-card-heading">
        <p className="signal-section-kicker">After the evidence</p>
        <h3 id="readiness-card-heading">Readiness and decision</h3>
        <dl>
          <div><dt>System readiness</dt><dd><ReadinessBand status={report.status} /></dd></div>
          <div><dt>Criteria needing judgment</dt><dd>{report.pending_review_count}</dd></div>
          <div><dt>Open integrity findings</dt><dd>{open.length}</dd></div>
          <div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div>
        </dl>
        <div className="signal-document-readiness-card__actions"><Button variant="secondary" onClick={onDecision}>Review readiness and decision</Button><ActionLink to={`/report/${report.check_run_id}`} variant="quiet">Open full readiness report</ActionLink></div>
      </section>
    </div>
  );
}

function SelectedFinding({ finding, total, locationFlagId, region, previous, next, onBack, onPrevious, onNext, onLocation, onExplore }: {
  finding: ReviewFinding;
  total: number;
  locationFlagId: number;
  region: FlagRegionOut | null;
  previous: ReviewFinding | null;
  next: ReviewFinding | null;
  onBack: () => void;
  onPrevious: () => void;
  onNext: () => void;
  onLocation: (flagId: number) => void;
  onExplore: () => void;
}) {
  const { data: flag, isPending, isError, refetch } = useFlag(locationFlagId);
  if (isPending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading Finding {finding.number}.</span><i /><i /></div>;
  if (isError || !flag) return <Alert title="Finding details could not be loaded" tone="error" role="alert"><p>The manuscript and review queue are still available. Try loading this finding again.</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button><Button variant="quiet" onClick={onBack}>Back to review queue</Button></Alert>;

  const { showsManuscriptQuote, reasoningText, showsManuscriptEvidence, isResubmission } = evidenceDisplayState(flag);
  const precision = regionPrecision(region);
  const comparative = flag.check_kind === "originality_reuse";
  const kindMeta = checkKindMeta(flag.check_kind);
  const verificationPrompt = flag.passage_pair
    ? kindMeta.verificationPrompt
    : kindMeta.verificationPromptWithoutComparison ?? kindMeta.verificationPrompt;
  const title = findingTitle(finding);
  const locationIndex = finding.flags.findIndex((member) => member.id === locationFlagId);

  return (
    <article className="signal-document-selected" aria-labelledby="selected-finding-heading">
      <Button variant="quiet" onClick={onBack}>Back to review queue</Button>
      <header className="signal-document-selected__header">
        <p className="signal-section-kicker">Finding {finding.number} of {total}</p>
        <h2 id="selected-finding-heading" tabIndex={-1}>{title}</h2>
        <div className="signal-document-selected__states"><span className={`signal-severity signal-severity--${finding.severity}`}>{severityLabel(finding.severity)}</span><span className="signal-resolution-state">{finding.isResolved ? "Resolved" : "Open"}</span></div>
        {(previous || next) && <div className="signal-document-finding-nav" role="group" aria-label="Move between review findings"><Button variant="quiet" disabled={!previous} onClick={onPrevious}>Previous finding</Button><Button variant="quiet" disabled={!next} onClick={onNext}>Next finding</Button></div>}
      </header>
      <dl className="signal-document-finding-facts">
        <div><dt>Check</dt><dd>{kindMeta.eyebrow}</dd></div>
        <div><dt>How exact is this location?</dt><dd>{precision.label}</dd></div>
        <div><dt>Recorded location</dt><dd>{instructorAnchor(flag.page_anchor)}</dd></div>
        <div><dt>Evidence type</dt><dd>{comparative ? flag.passage_pair ? "Recorded passage comparison" : "Comparison without a stored pair" : "Single-location check"}</dd></div>
      </dl>
      <p className="signal-document-precision-note"><strong>{precision.label}.</strong> {precision.helper}</p>
      {finding.flags.length > 1 && (
        <section className="signal-document-locations" aria-labelledby="finding-locations-heading">
          <h3 id="finding-locations-heading">Recorded locations and scopes</h3>
          <p>Location {locationIndex + 1} of {finding.flags.length}. These records are presented as one finding; none of the stored data was removed.</p>
          <div role="group" aria-label={`Locations for Finding ${finding.number}`}>{finding.flags.map((member, index) => <button key={member.id} type="button" aria-pressed={member.id === locationFlagId} onClick={() => onLocation(member.id)}>Location {index + 1}: {instructorAnchor(member.page_anchor)}</button>)}</div>
        </section>
      )}
      <section aria-labelledby="noticed-heading" className="signal-document-detail-section">
        <h3 id="noticed-heading">What VERIDICAL noticed</h3>
        <p>{title}. This is a possible issue for instructor review, not a final determination.</p>
        {!showsManuscriptQuote && <p><strong>Recorded check summary:</strong> {systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}</p>}
      </section>
      <section aria-labelledby="evidence-heading" className="signal-document-detail-section">
        <h3 id="evidence-heading">Evidence to compare or check</h3>
        {flag.passage_pair && <p className="signal-field-hint"><strong>Highlight key:</strong> Yellow marks the selected manuscript location. Shaded wording below appears in both stored passages.</p>}
        {flag.passage_pair && <PassagePairPanel pair={flag.passage_pair} ownAnchor={instructorAnchor(flag.page_anchor)} variant="flag" />}
        {!flag.passage_pair && showsManuscriptQuote && <blockquote><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{instructorAnchor(flag.page_anchor)}</cite></blockquote>}
        {!comparative && <p className="signal-field-hint">This check evaluates one manuscript location. A second passage does not apply.</p>}
      </section>
      {flag.passage_pair && <Button variant="quiet" onClick={onExplore}>See other passage matches</Button>}
      {!showsManuscriptEvidence && (
        <Alert title={isResubmission ? "No passage pair is available" : "No supporting passage was stored"} tone={isResubmission ? "info" : "warning"}>
          {isResubmission
            ? "This finding was recorded from a whole-manuscript check. VERIDICAL did not store two passages that can be compared side by side. Review the recorded scope and technical details before deciding."
            : flag.evidence_unavailable
              ? "VERIDICAL could not show a passage for this finding. Its severity was reduced. Do not treat this as source-verified evidence."
              : "VERIDICAL did not store two passages that can be compared side by side. Review the recorded scope and technical details before deciding."}
        </Alert>
      )}
      <section aria-labelledby="verify-heading" className="signal-document-detail-section signal-document-detail-section--verify"><h3 id="verify-heading">What the instructor should verify</h3><p>{verificationPrompt}</p></section>
      <details className="signal-document-recorded-reasoning"><summary>Recorded reasoning and technical details</summary><p>This is the system's full recorded explanation. It is supporting information, not an instructor decision.</p>{reasoningText ? <p>{systemFindingCopy(reasoningText, flag.ai_verdict_summary, "reasoning")}</p> : <p>No additional recorded reasoning is available.</p>}</details>
      <ActionLink to={`/flags/${flag.id}`} variant="secondary">Review instructor actions for this location</ActionLink>
    </article>
  );
}

function ReportUnavailable({ checkRunId, error, onRetry }: { checkRunId: number; error: unknown; onRetry: () => void }) {
  if (error instanceof ApiError && error.code === "conflict") return <Alert title="This review is not ready yet" tone="info" role="alert"><p>The check has not produced a complete readiness record.</p><ActionLink to={`/checks/${checkRunId}`} variant="secondary">View check progress</ActionLink></Alert>;
  return <Alert title="Could not load review details" tone="error" role="alert"><p>VERIDICAL could not load the recorded review. The source pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={onRetry}>Try review again</Button><ActionLink to="/dashboard?queue=needs_review" variant="quiet">Return to Review Desk</ActionLink></Alert>;
}

function ReadinessDecision({ checkRunId, report, pending, error, onRetry, onBack }: { checkRunId: number; report: ReportOut | undefined; pending: boolean; error: unknown; onRetry: () => void; onBack: () => void }) {
  if (pending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading readiness and decision.</span><i /><i /></div>;
  if (!report) return <ReportUnavailable checkRunId={checkRunId} error={error} onRetry={onRetry} />;

  const identity = manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename);
  const coverage = coverageItems(report);
  const assessed = report.results.filter((row) => row.outcome !== "escalated");
  return (
    <div className="signal-document-analysis">
      <Button variant="quiet" onClick={onBack}>Back to review queue</Button>
      <SignalEscalatedPanel checkRunId={report.check_run_id} />
      <section className="signal-document-analysis__summary" aria-labelledby="analysis-heading">
        <div className="signal-document-inspector__heading"><p className="signal-section-kicker">After the evidence</p><h2 id="analysis-heading">Readiness and decision</h2><p>Use this summary after reviewing the evidence. The instructor records the final decision.</p></div>
        <div className="signal-document-analysis__readiness">
          <div><span>System readiness</span><ReadinessBand status={report.status} /></div>
          <p>{report.reason ?? "This band comes from the recorded criterion outcomes and integrity findings."}</p>
          <dl><div><dt>Criteria needing your judgment</dt><dd>{report.pending_review_count}</dd></div><div><dt>Open high-severity findings</dt><dd>{report.unresolved_high_flag_count}</dd></div><div><dt>Instructor decision</dt><dd>{report.decision ? DECISION_LABEL[report.decision] : "Not recorded"}</dd></div></dl>
          <p className="signal-field-hint">{identity.primary} was checked against {report.rubric_title}.</p>
          <ActionLink to={`/report/${report.check_run_id}`} variant="secondary">Open full readiness report</ActionLink>
        </div>
      </section>
      {report.llm_mode !== "real" && <Alert title={report.llm_mode === "fake" ? "Test-mode AI results" : "AI mode could not be verified"} tone="warning">{report.llm_mode === "fake" ? "This review was produced with fixture responses, not real AI grading. Do not treat it as a finding about the manuscript." : "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously."}</Alert>}
      {coverage.length > 0 && <CoverageStatement state="partial" headingId="workspace-coverage-heading" heading="Not every check finished" intro="Nothing skipped here is presented as passed. Each item below names what did not finish and how to close it." items={coverage} />}
      <details className="signal-document-analysis__criteria"><summary>Criterion record, {assessed.length} assessed</summary><SignalCriteriaResults results={assessed} checkRunId={report.check_run_id} /></details>
      <SignalDecisionPanel report={report} manuscriptLabel={identity.primary} />
      <div className="signal-document-analysis__footer"><ActionLink to={`/report/${report.check_run_id}`} variant="secondary">Open full readiness report</ActionLink></div>
    </div>
  );
}

function ReviewHome({ checkRunId, report, reportPending, reportError, onReportRetry, findings, regions, flagsPending, flagsError, filter, invalidSelection, onFilter, onSelect, onExplore, onDecision }: {
  checkRunId: number;
  report: ReportOut | undefined;
  reportPending: boolean;
  reportError: unknown;
  onReportRetry: () => void;
  findings: ReviewFinding[];
  regions: FlagRegionOut[];
  flagsPending: boolean;
  flagsError: boolean;
  filter: ReviewFilter;
  invalidSelection: boolean;
  onFilter: (filter: ReviewFilter) => void;
  onSelect: (id: number) => void;
  onExplore: () => void;
  onDecision: () => void;
}) {
  if (reportPending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading review queue.</span><i /><i /></div>;
  if (!report) return <ReportUnavailable checkRunId={checkRunId} error={reportError} onRetry={onReportRetry} />;
  return <ReviewQueue findings={findings} regions={regions} report={report} pending={flagsPending} error={flagsError} filter={filter} invalidSelection={invalidSelection} onFilter={onFilter} onSelect={onSelect} onExplore={onExplore} onDecision={onDecision} />;
}

export function SignalDocumentViewerPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const validId = Number.isInteger(id) && id > 0;
  const [searchParams, setSearchParams] = useSearchParams();
  const rawSelection = searchParams.get("finding") ?? searchParams.get("flag");
  const parsedSelection = rawSelection ? Number(rawSelection) : null;
  const requestedFlagId = parsedSelection && Number.isInteger(parsedSelection) && parsedSelection > 0 ? parsedSelection : null;
  const recordedAnchor = searchParams.get("anchor");
  const anchorRegion = criterionAnchorRegion(recordedAnchor);
  const view = searchParams.get("view");
  const isExploring = view === "matches" || searchParams.get("panel") === "explore";
  const isDecision = view === "decision";
  const includeReferenceList = searchParams.get("ref") === "1";
  const includeBlockQuote = searchParams.get("quote") === "1";
  const expandedMatchId = searchParams.get("match");
  const activeTab = searchParams.get("pane") === "review" || searchParams.get("pane") === "analysis" ? "review" : "manuscript";
  const filter = parseFilter(searchParams.get("filter"));
  const headingRef = useRef<HTMLHeadingElement>(null);
  const manuscriptTabRef = useRef<HTMLButtonElement>(null);
  const reviewTabRef = useRef<HTMLButtonElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const queueScrollRef = useRef(0);
  const queueFocusRef = useRef<number | null>(null);
  const restoreQueueRef = useRef(false);
  useRouteFocus("Manuscript review - VERIDICAL", headingRef);

  const viewerQuery = useManuscriptViewer(id);
  const reportQuery = useReport(id);
  const flagsQuery = useFlags(id);
  const excludedMatchesQuery = useExcludedReuseMatches(id, { includeReferenceList, includeBlockQuote });
  const viewer = viewerQuery.data;
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const paragraphsQuery = useManuscriptParagraphs(id, isDocx);
  const findings = useMemo(() => buildReviewFindings(flagsQuery.data ?? []), [flagsQuery.data]);
  const numbersByFlagId = useMemo(() => findingNumberByFlagId(findings), [findings]);
  const selectedFinding = findingForFlag(findings, requestedFlagId);
  const rawLocation = searchParams.get("location");
  const parsedLocation = rawLocation ? Number(rawLocation) : null;
  const selectedLocationId = selectedFinding
    ? selectedFinding.flags.some((flag) => flag.id === parsedLocation)
      ? parsedLocation!
      : requestedFlagId && selectedFinding.flags.some((flag) => flag.id === requestedFlagId)
        ? requestedFlagId
        : selectedFinding.representative.id
    : null;
  const invalidSelection = rawSelection !== null && !flagsQuery.isPending && (requestedFlagId === null || !selectedFinding);
  const filteredFindings = findings.filter((finding) => filter === "all" || (filter === "resolved" ? finding.isResolved : !finding.isResolved));
  const navigationFindings = selectedFinding && filteredFindings.some((finding) => finding.key === selectedFinding.key) ? filteredFindings : findings;
  const selectedNavigationIndex = selectedFinding ? navigationFindings.findIndex((finding) => finding.key === selectedFinding.key) : -1;
  const previousFinding = selectedNavigationIndex > 0 ? navigationFindings[selectedNavigationIndex - 1] : null;
  const nextFinding = selectedNavigationIndex >= 0 && selectedNavigationIndex < navigationFindings.length - 1 ? navigationFindings[selectedNavigationIndex + 1] : null;

  function update(change: (next: URLSearchParams) => void, replace = false) {
    const next = new URLSearchParams(searchParams);
    change(next);
    setSearchParams(next, { replace });
  }

  function selectFinding(flagId: number, replace = false) {
    const finding = findingForFlag(findings, flagId);
    if (!finding) return;
    queueScrollRef.current = inspectorRef.current?.scrollTop ?? 0;
    queueFocusRef.current = finding.representative.id;
    update((next) => {
      next.set("view", "finding");
      next.set("finding", String(finding.representative.id));
      next.set("location", String(flagId));
      next.delete("flag");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
      next.delete("pane");
    }, replace);
  }

  function showQueue() {
    restoreQueueRef.current = true;
    update((next) => {
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
    update((next) => {
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

  function openDecision() {
    update((next) => {
      next.set("view", "decision");
      next.set("pane", "review");
      next.delete("finding");
      next.delete("flag");
      next.delete("location");
      next.delete("anchor");
      next.delete("panel");
      next.delete("match");
    });
  }

  function setFilter(value: ReviewFilter) {
    update((next) => {
      if (value === "open") next.delete("filter");
      else next.set("filter", value);
      next.set("view", "queue");
      next.set("pane", "review");
    }, true);
  }

  function setToggle(key: "ref" | "quote", value: boolean) {
    update((next) => { if (value) next.set(key, "1"); else next.delete(key); });
  }

  function setMatch(value: string | null) {
    update((next) => { if (value) { next.set("match", value); next.delete("pane"); } else next.delete("match"); });
  }

  function selectPane(pane: "manuscript" | "review", moveFocus = false) {
    update((next) => { if (pane === "review") next.set("pane", "review"); else next.delete("pane"); }, true);
    if (moveFocus) (pane === "manuscript" ? manuscriptTabRef : reviewTabRef).current?.focus();
  }

  function showSelectedReview() {
    selectPane("review");
    requestAnimationFrame(() => document.getElementById("selected-finding-heading")?.focus());
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
  const selectedRegion = isExploring
    ? expandedMatch?.own_region ?? null
    : selectedLocationId
      ? viewer?.regions.find((region) => region.flag_id === selectedLocationId) ?? null
      : anchorRegion;
  const requestedPage = selectedRegion?.page ?? null;
  const browseRegions = representativeRegions(findings, viewer?.regions ?? []);
  const visibleRegions = selectedRegion?.flag_id === CRITERION_ANCHOR_REGION_ID
    ? [...browseRegions, selectedRegion]
    : isExploring && expandedMatch
      ? [...browseRegions, expandedMatch.own_region]
      : selectedFinding && selectedRegion
        ? [selectedRegion]
        : browseRegions;
  const selectedRegionId = selectedRegion?.flag_id ?? null;
  const selectedSummary = selectedFinding?.flags.find((flag) => flag.id === selectedLocationId) ?? selectedFinding?.representative ?? null;
  const selectedPrecision = selectedFinding ? regionPrecision(selectedRegion) : null;
  const reviewCount = findings.filter((finding) => !finding.isResolved).length + (reportQuery.data?.pending_review_count ?? 0);

  return (
    <div className="signal-route signal-page-flow signal-document-viewer">
      <header className="signal-route-header signal-document-header">
        <div><nav aria-label="Breadcrumb"><Link to="/dashboard?queue=needs_review">Review Desk</Link><span aria-hidden="true">/</span><span>Manuscript review</span></nav><h1 ref={headingRef} tabIndex={-1}>Manuscript review</h1>{viewer?.original_filename && <p className="signal-route-header__intro">{viewer.original_filename}</p>}</div>
        {validId && <ActionLink to={`/report/${id}`} variant="secondary">Open full readiness report</ActionLink>}
      </header>

      {!validId ? <Alert title="This manuscript address is invalid" tone="error" role="alert"><ActionLink to="/dashboard?queue=needs_review" variant="secondary">Return to Review Desk</ActionLink></Alert>
        : (
          <section className="signal-document-workbench" aria-label="Manuscript and review" data-source-available={viewer?.available === false ? "false" : "true"}>
            <div role="tablist" aria-label="Manuscript review view" className="signal-document-tabs">
              <button ref={manuscriptTabRef} id="manuscript-tab" type="button" role="tab" aria-controls="manuscript-panel" aria-selected={activeTab === "manuscript"} tabIndex={activeTab === "manuscript" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("manuscript")}>Manuscript</button>
              <button ref={reviewTabRef} id="review-tab" type="button" role="tab" aria-controls="review-panel" aria-label={reviewCount > 0 ? `Review, ${reviewCount} ${reviewCount === 1 ? "task" : "tasks"}` : "Review"} aria-selected={activeTab === "review"} tabIndex={activeTab === "review" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectPane("review")}>Review{reviewCount > 0 ? ` (${reviewCount})` : ""}</button>
            </div>
            <div className="signal-document-grid">
              <div id="manuscript-panel" role="tabpanel" aria-labelledby="manuscript-tab" data-active={activeTab === "manuscript"} className="signal-document-pane">
                {selectedFinding && selectedSummary && selectedPrecision && (
                  <div className="signal-document-selection-strip" role="status"><div><strong>Finding {selectedFinding.number} selected</strong><span>{instructorAnchor(selectedSummary.page_anchor)} · {selectedPrecision.label}</span></div><div><Button variant="secondary" onClick={showSelectedReview}>View review</Button><Button variant="quiet" onClick={showQueue}>Clear selection</Button></div></div>
                )}
                {recordedAnchor && !anchorRegion && <Alert title="The recorded criterion anchor could not be placed" tone="warning">Review the recorded excerpt in Review and inspect the manuscript manually.</Alert>}
                {rawSelection && requestedFlagId === null && <Alert title="The selected finding address is invalid" tone="warning">Showing the review queue instead.</Alert>}
                {viewerQuery.isPending ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>Loading stored source</span></div></div><div className="signal-document-scroll"><section role="status" aria-live="polite" aria-busy="true" className="signal-document-pdf-skeleton"><span>Loading full manuscript.</span><i /><i /><i /></section></div></div>
                  : viewerQuery.isError || !viewer ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>Stored source</span></div></div><div className="signal-document-scroll"><section className="signal-document-paper-state"><Alert title="The manuscript could not be loaded" tone="error" role="alert"><p>VERIDICAL could not load the stored source view. The Review pane loads separately and may also need to be retried.</p><Button variant="secondary" onClick={() => viewerQuery.refetch()}>Try source again</Button><ActionLink to="/dashboard?queue=needs_review" variant="quiet">Return to Review Desk</ActionLink></Alert></section></div></div>
                    : !viewer.available ? <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>{viewer.original_filename ?? "Stored source"}</span></div></div><div className="signal-document-scroll"><section className="signal-document-unavailable"><p className="signal-section-kicker">Stored source unavailable</p><h2>Source manuscript unavailable</h2><p>{viewer.unavailable_reason}</p><p>You can still review the recorded evidence and readiness details, but VERIDICAL cannot show the full manuscript.</p><ActionLink to={`/report/${id}`} variant="brand">Open full readiness report</ActionLink></section></div></div>
                      : viewer.source_format === "pdf" ? <PdfPane fileUrl={`${BASE_URL}/check-runs/${id}/document/file`} regions={visibleRegions} flags={flagsQuery.data ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFinding} requestedPage={requestedPage} findingNumbers={numbersByFlagId} />
                        : viewer.source_format === "docx" ? <DocxPane paragraphs={paragraphsQuery.data?.paragraphs} paragraphsPending={paragraphsQuery.isPending} paragraphsError={paragraphsQuery.isError} onRetry={() => paragraphsQuery.refetch()} regions={visibleRegions} flags={flagsQuery.data ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFinding} isVisible={activeTab === "manuscript"} findingNumbers={numbersByFlagId} />
                          : <div className="signal-document-source"><div className="signal-document-toolbar"><div className="signal-document-toolbar__identity"><strong>Full manuscript</strong><span>{viewer.original_filename ?? "Unsupported source"}</span></div></div><div className="signal-document-scroll"><section className="signal-document-paper-state"><Alert title="This manuscript format cannot be displayed here" tone="warning"><p>Use the recorded excerpts and anchors in Review, or open the full readiness report.</p><ActionLink to={`/report/${id}`} variant="secondary">Open full readiness report</ActionLink></Alert></section></div></div>}
              </div>
              <aside ref={inspectorRef} id="review-panel" role="tabpanel" aria-labelledby="review-tab" data-active={activeTab === "review"} className="signal-document-inspector">
                {isExploring ? <ReuseExplorePanel flags={flagsQuery.data} onSelectFlag={selectFinding} onBack={showQueue} includeReferenceList={includeReferenceList} includeBlockQuote={includeBlockQuote} onToggleReferenceList={(value) => setToggle("ref", value)} onToggleBlockQuote={(value) => setToggle("quote", value)} excludedMatchesQuery={excludedMatchesQuery} expandedMatchId={expandedMatchId} onExpandMatch={setMatch} />
                  : isDecision ? <ReadinessDecision checkRunId={id} report={reportQuery.data} pending={reportQuery.isPending} error={reportQuery.error} onRetry={() => reportQuery.refetch()} onBack={showQueue} />
                    : selectedFinding && selectedLocationId ? <SelectedFinding finding={selectedFinding} total={findings.length} locationFlagId={selectedLocationId} region={selectedRegion} previous={previousFinding} next={nextFinding} onBack={showQueue} onPrevious={() => previousFinding && selectFinding(previousFinding.representative.id, true)} onNext={() => nextFinding && selectFinding(nextFinding.representative.id, true)} onLocation={selectFinding} onExplore={openExplore} />
                      : <ReviewHome checkRunId={id} report={reportQuery.data} reportPending={reportQuery.isPending} reportError={reportQuery.error} onReportRetry={() => reportQuery.refetch()} findings={findings} regions={viewer?.regions ?? []} flagsPending={flagsQuery.isPending} flagsError={flagsQuery.isError} filter={filter} invalidSelection={invalidSelection} onFilter={setFilter} onSelect={selectFinding} onExplore={openExplore} onDecision={openDecision} />}
              </aside>
            </div>
          </section>
        )}
    </div>
  );
}
