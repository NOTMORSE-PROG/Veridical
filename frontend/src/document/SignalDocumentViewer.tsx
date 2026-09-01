import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { BASE_URL } from "../api/client";
import type { FlagRegionOut, FlagSummaryOut } from "../api/types";
import { CRITERION_ANCHOR_REGION_ID } from "../config/ui";
import { checkKindMeta } from "../domain/checkKind";
import { problemLabel } from "../domain/problemLabel";
import { systemFindingCopy } from "../domain/systemFindingCopy";
import { truncateAtWord } from "../format/text";
import { useFlag } from "../flags/useFlag";
import {
  useExcludedReuseMatches,
  useFlags,
  useManuscriptParagraphs,
  useManuscriptViewer,
} from "../report/useReport";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { DocxPane } from "./DocxPane";
import { PassagePairPanel } from "./PassagePairPanel";
import { PdfPane } from "./PdfPane";
import { regionCopy } from "./regionCopy";
import { ReuseExplorePanel } from "./ReuseExplorePanel";

const SEVERITY_LABEL: Record<FlagSummaryOut["severity"], string> = {
  high: "High severity",
  med: "Medium severity",
  low: "Low severity",
};

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

function EvidenceBrowse({ flags, pending, error, onSelect, onExplore }: {
  flags: FlagSummaryOut[] | undefined;
  pending: boolean;
  error: boolean;
  onSelect: (id: number) => void;
  onExplore: () => void;
}) {
  if (pending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading evidence…</span><i /><i /></div>;
  if (error || !flags) return <Alert title="Could not load integrity evidence" tone="error" role="alert">Return to the readiness report or try this page again.</Alert>;
  return (
    <div className="signal-document-evidence-list">
      <div className="signal-document-inspector__heading"><p className="signal-section-kicker">Evidence in this manuscript</p><h2>Integrity signals</h2><p>Choose a signal to place its recorded location in the source. Severity is never communicated by highlight color alone.</p></div>
      {flags.length ? <ul>{flags.map((flag) => <li key={flag.id}><button type="button" onClick={() => onSelect(flag.id)}><span className="signal-document-signal__top"><span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span>{flag.overridden && <span className="signal-resolution-state">Resolved</span>}</span><strong>{problemLabel(flag.problem_kind) ?? flag.criterion_text ?? checkKindMeta(flag.check_kind).title}</strong><span>{truncateAtWord(systemFindingCopy(flag.evidence_excerpt, flag.problem_kind, "evidence"), 160)}</span><small>{flag.page_anchor}</small></button></li>)}</ul> : <Alert title="No integrity signals" tone="success">This run has no integrity signals. You can still review criterion evidence in the readiness report.</Alert>}
      <Button variant="quiet" onClick={onExplore}>Explore passage matches not included in readiness</Button>
    </div>
  );
}

function SelectedEvidence({ flagId, region, onBack, onExplore }: {
  flagId: number;
  region: FlagRegionOut | null;
  onBack: () => void;
  onExplore: () => void;
}) {
  const { data: flag, isPending, isError, refetch } = useFlag(flagId);
  if (isPending) return <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading selected evidence…</span><i /><i /></div>;
  if (isError || !flag) return <Alert title="Could not load this signal" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>;
  const placement = region ? regionCopy(region) : "This evidence location could not be placed in the stored document. Review the recorded excerpt and anchor here.";
  return (
    <div className="signal-document-selected">
      <Button variant="quiet" onClick={onBack}>Back to all evidence</Button>
      <div className="signal-document-inspector__heading"><p className="signal-section-kicker">Possible inconsistency</p><h2>{checkKindMeta(flag.check_kind).title}</h2><span className={`signal-severity signal-severity--${flag.severity}`}>{SEVERITY_LABEL[flag.severity]}</span></div>
      {placement && <Alert title="Source placement" tone="info">{placement}</Alert>}
      <blockquote><span>“{systemFindingCopy(flag.evidence_excerpt, flag.ai_verdict_summary, "evidence")}”</span><cite>{flag.page_anchor}</cite></blockquote>
      {flag.ai_reasoning && <p><strong>Recorded reasoning:</strong> {systemFindingCopy(flag.ai_reasoning, flag.ai_verdict_summary, "reasoning")}</p>}
      {flag.passage_pair && <PassagePairPanel pair={flag.passage_pair} ownAnchor={flag.page_anchor} variant="flag" />}
      {flag.passage_pair && <Button variant="quiet" onClick={onExplore}>See other passage matches</Button>}
      <ActionLink to={`/flags/${flag.id}`} variant="secondary">Review full signal and instructor actions</ActionLink>
    </div>
  );
}

export function SignalDocumentViewerPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const validId = Number.isInteger(id) && id > 0;
  const [searchParams, setSearchParams] = useSearchParams();
  const rawFlagId = searchParams.get("flag");
  const parsedFlagId = rawFlagId ? Number(rawFlagId) : null;
  const selectedFlagId = parsedFlagId && Number.isInteger(parsedFlagId) && parsedFlagId > 0 ? parsedFlagId : null;
  const recordedAnchor = searchParams.get("anchor");
  const anchorRegion = criterionAnchorRegion(recordedAnchor);
  const isExploring = searchParams.get("panel") === "explore";
  const includeReferenceList = searchParams.get("ref") === "1";
  const includeBlockQuote = searchParams.get("quote") === "1";
  const expandedMatchId = searchParams.get("match");
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Source manuscript - VERIDICAL", headingRef);

  const viewerQuery = useManuscriptViewer(id);
  const flagsQuery = useFlags(id);
  const excludedMatchesQuery = useExcludedReuseMatches(id, { includeReferenceList, includeBlockQuote });
  const viewer = viewerQuery.data;
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const paragraphsQuery = useManuscriptParagraphs(id, isDocx);

  function update(change: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    change(next);
    setSearchParams(next);
  }
  function selectFlag(flagId: number) {
    update((next) => { next.set("flag", String(flagId)); next.delete("anchor"); next.delete("panel"); next.delete("match"); });
  }
  function showAllEvidence() {
    update((next) => { next.delete("flag"); next.delete("anchor"); next.delete("panel"); next.delete("ref"); next.delete("quote"); next.delete("match"); });
  }
  function openExplore() {
    update((next) => { next.delete("flag"); next.delete("anchor"); next.set("panel", "explore"); next.delete("match"); });
  }
  function setToggle(key: "ref" | "quote", value: boolean) {
    update((next) => { if (value) next.set(key, "1"); else next.delete(key); });
  }
  function setMatch(value: string | null) {
    update((next) => { if (value) next.set("match", value); else next.delete("match"); });
  }

  const expandedMatch = excludedMatchesQuery.data?.matches.find((match) => match.id === expandedMatchId) ?? null;
  const selectedRegion = isExploring
    ? expandedMatch?.own_region ?? null
    : selectedFlagId
      ? viewer?.regions.find((region) => region.flag_id === selectedFlagId) ?? null
      : anchorRegion;
  const requestedPage = selectedRegion?.page ?? null;
  const visibleRegions = selectedRegion?.flag_id === CRITERION_ANCHOR_REGION_ID
    ? [...(viewer?.regions ?? []), selectedRegion]
    : isExploring && expandedMatch
      ? [...(viewer?.regions ?? []), expandedMatch.own_region]
      : viewer?.regions ?? [];
  const selectedRegionId = selectedRegion?.flag_id ?? null;
  const [activeTab, setActiveTab] = useState<"document" | "evidence">(selectedRegion ? "document" : "evidence");

  useEffect(() => {
    if (selectedRegion) setActiveTab("document");
  }, [selectedRegion]);

  function tabKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    setActiveTab((current) => current === "document" ? "evidence" : "document");
  }

  return (
    <div className="signal-route signal-page-flow signal-document-viewer">
      <header className="signal-route-header signal-document-header">
        <div><nav aria-label="Breadcrumb"><Link to={`/report/${id}`}>Readiness report</Link><span aria-hidden="true">/</span><span>Source manuscript</span></nav><p className="signal-eyebrow">Review · Source evidence</p><h1 ref={headingRef} tabIndex={-1}>Source manuscript</h1>{viewer?.original_filename && <p className="signal-route-header__intro">{viewer.original_filename}</p>}</div>
        {validId && <ActionLink to={`/report/${id}`} variant="secondary">Back to readiness report</ActionLink>}
      </header>

      {!validId ? <Alert title="This manuscript address is invalid" tone="error" role="alert"><ActionLink to="/dashboard?queue=needs_review" variant="secondary">Return to Review Desk</ActionLink></Alert>
        : viewerQuery.isPending ? <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading the source manuscript…</span><i /><i /></div>
        : viewerQuery.isError || !viewer ? <Alert title="The source manuscript could not be loaded" tone="error" role="alert"><Button variant="secondary" onClick={() => viewerQuery.refetch()}>Try again</Button></Alert>
        : !viewer.available ? <section className="signal-document-unavailable"><p className="signal-section-kicker">Stored source unavailable</p><h2>The stored manuscript is unavailable</h2><p>{viewer.unavailable_reason}</p><p>The readiness report and recorded evidence remain available. Missing source content is not treated as verified.</p><ActionLink to={`/report/${id}`} variant="brand">Return to readiness report</ActionLink></section>
        : (
          <section className="signal-document-workbench" aria-label="Source manuscript and evidence">
            {recordedAnchor && !anchorRegion && <Alert title="The recorded criterion anchor could not be placed" tone="warning">Review the criterion's excerpt in the readiness report and inspect this source manually.</Alert>}
            {rawFlagId && selectedFlagId === null && <Alert title="The selected signal address is invalid" tone="warning">Showing all evidence instead.</Alert>}
            <div role="tablist" aria-label="Source manuscript view" className="signal-document-tabs">
              <button type="button" role="tab" aria-selected={activeTab === "document"} tabIndex={activeTab === "document" ? 0 : -1} onKeyDown={tabKey} onClick={() => setActiveTab("document")}>Document</button>
              <button type="button" role="tab" aria-selected={activeTab === "evidence"} tabIndex={activeTab === "evidence" ? 0 : -1} onKeyDown={tabKey} onClick={() => setActiveTab("evidence")}>Evidence{flagsQuery.data ? ` (${flagsQuery.data.length})` : ""}</button>
            </div>
            <div className="signal-document-grid">
              <div role="tabpanel" aria-label="Document" data-active={activeTab === "document"} className="signal-document-pane">
                {viewer.source_format === "pdf" ? <PdfPane fileUrl={`${BASE_URL}/check-runs/${id}/document/file`} regions={visibleRegions} flags={flagsQuery.data ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFlag} requestedPage={requestedPage} />
                  : viewer.source_format === "docx" ? <DocxPane paragraphs={paragraphsQuery.data?.paragraphs} paragraphsPending={paragraphsQuery.isPending} paragraphsError={paragraphsQuery.isError} onRetry={() => paragraphsQuery.refetch()} regions={visibleRegions} flags={flagsQuery.data ?? []} selectedFlagId={selectedRegionId} onSelectFlag={selectFlag} isVisible={activeTab === "document"} />
                    : <Alert title="This source format cannot be displayed" tone="warning">Return to the readiness report to review the recorded excerpts.</Alert>}
              </div>
              <aside role="tabpanel" aria-label="Evidence" data-active={activeTab === "evidence"} className="signal-document-inspector">
                {isExploring ? <ReuseExplorePanel flags={flagsQuery.data} onSelectFlag={selectFlag} onBack={showAllEvidence} includeReferenceList={includeReferenceList} includeBlockQuote={includeBlockQuote} onToggleReferenceList={(value) => setToggle("ref", value)} onToggleBlockQuote={(value) => setToggle("quote", value)} excludedMatchesQuery={excludedMatchesQuery} expandedMatchId={expandedMatchId} onExpandMatch={setMatch} />
                  : selectedFlagId ? <SelectedEvidence flagId={selectedFlagId} region={selectedRegion} onBack={showAllEvidence} onExplore={openExplore} />
                    : <EvidenceBrowse flags={flagsQuery.data} pending={flagsQuery.isPending} error={flagsQuery.isError} onSelect={selectFlag} onExplore={openExplore} />}
              </aside>
            </div>
          </section>
        )}
    </div>
  );
}
