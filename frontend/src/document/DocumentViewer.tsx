// V-065 (AC1-4, 6, 7 -- AC5/F7.4 passage-pair is a follow-up ticket, see
// tickets/V8-real-use/open/V-065.md's 2026-08-19 scope-split note): the
// split-pane manuscript viewer. Document pane on the left (PDF.js for PDF
// sources, a reconstructed-text pane for DOCX), analysis pane on the
// right (the existing flags list, browse or one flag's detail).
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { BASE_URL } from "../api/client";
import { AnchorPill } from "../components/AnchorPill";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { StatusPill } from "../components/StatusPill";
import { TabPanel, Tabs } from "../components/Tabs";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { truncateAtWord } from "../format/text";
import { useFlag } from "../flags/useFlag";
import { useRouteFocus } from "../routing/useRouteFocus";
import {
  useExcludedReuseMatches,
  useFlags,
  useManuscriptParagraphs,
  useManuscriptViewer,
} from "../report/useReport";
import type { FlagRegionOut, FlagSummaryOut } from "../api/types";
import { DocxPane } from "./DocxPane";
import { PassagePairPanel } from "./PassagePairPanel";
import { PdfPane } from "./PdfPane";
import { regionCopy } from "./regionCopy";
import { ReuseExplorePanel } from "./ReuseExplorePanel";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function BrowseFlags({
  flags,
  isPending,
  isError,
  onSelect,
  onExplore,
}: {
  flags: FlagSummaryOut[] | undefined;
  isPending: boolean;
  isError: boolean;
  onSelect: (flagId: number) => void;
  onExplore: () => void;
}) {
  if (isPending) {
    return (
      <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
        Loading flags.
      </p>
    );
  }
  if (isError || !flags) {
    return (
      <p role="alert" className="p-4 text-sm text-status-attention-text">
        This report's flags couldn't be loaded.
      </p>
    );
  }
  if (flags.length === 0) {
    return (
      <div>
        <p className="p-4 text-sm text-ink-secondary">
          No integrity flags on this run. VERIDICAL's four integrity checks (internal agreement,
          citation integrity, statistical forensics, and originality/reuse) found nothing to
          report.
        </p>
        <p className="border-t border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
          VERIDICAL also compares shorter passages, about 150 words, against its shared library,
          separately from the whole-document and section comparisons above.{" "}
          <button type="button" onClick={onExplore} className="font-medium text-link underline hover:text-link-hover">
            Explore passage matches
          </button>
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
        Citation and originality checks skip the reference list itself when comparing text, so a
        shared bibliography entry doesn't get flagged as reused content.
      </p>
      <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
        VERIDICAL also compares shorter passages, about 150 words, against its shared library,
        separately from the whole-document and section comparisons above.{" "}
        <button type="button" onClick={onExplore} className="font-medium text-link underline hover:text-link-hover">
          Explore passage matches
        </button>
      </p>
      {flags.map((flag) => (
        <button
          key={flag.id}
          type="button"
          onClick={() => onSelect(flag.id)}
          className="flex w-full flex-col gap-1.5 border-b border-border px-3.5 py-3 text-left text-sm hover:bg-status-neutral-bg"
        >
          <p className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
            {CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}
          </p>
          <p className={flag.overridden ? "text-ink-tertiary" : "text-ink-secondary"}>
            {truncateAtWord(flag.evidence_excerpt, 140)}
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <AnchorPill anchor={flag.page_anchor} />
            <SeverityTag severity={flag.severity as Severity} />
            {flag.overridden && <StatusPill tone="neutral">Overridden</StatusPill>}
          </div>
        </button>
      ))}
    </div>
  );
}

function FlagDetailCard({
  flagId,
  onBack,
  onExplore,
}: {
  flagId: number;
  onBack: () => void;
  onExplore: () => void;
}) {
  const { data: flag, isPending, isError } = useFlag(flagId);

  if (isPending) {
    return (
      <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
        Loading flag.
      </p>
    );
  }
  if (isError || !flag) {
    return (
      <p role="alert" className="p-4 text-sm text-status-attention-text">
        This flag couldn't be loaded.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3.5 text-sm">
      <button type="button" onClick={onBack} className="w-fit text-sm font-medium text-link underline hover:text-link-hover">
        ← All flags
      </button>
      <p className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
        {CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        <SeverityTag severity={flag.severity as Severity} />
        {flag.overridden && <StatusPill tone="neutral">Overridden</StatusPill>}
      </div>
      <section aria-label="Evidence" className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Evidence</span>
        <blockquote className="rounded-lg border border-border bg-page px-4 py-3 text-sm break-words text-ink">
          {flag.evidence_excerpt}
        </blockquote>
        <AnchorPill anchor={flag.page_anchor} />
        {flag.ai_reasoning && <p className="text-sm text-ink-secondary">{flag.ai_reasoning}</p>}
      </section>
      {flag.passage_pair && (
        <>
          <PassagePairPanel pair={flag.passage_pair} ownAnchor={flag.page_anchor} variant="flag" />
          <button type="button" onClick={onExplore} className="w-fit text-sm font-medium text-link underline hover:text-link-hover">
            See other passage matches for this manuscript
          </button>
        </>
      )}
      <Link to={`/flags/${flag.id}`} className="w-fit text-sm font-medium text-link underline hover:text-link-hover">
        Open as full page
      </Link>
    </div>
  );
}

// Switches the analysis pane between browsing flags, one flag's detail,
// and the F7.4 exploration panel -- factored out since both the PDF and
// DOCX layout branches below render the identical three-way choice.
function AnalysisPane({
  selectedFlagId,
  isExploring,
  flags,
  flagsPending,
  flagsError,
  selectedRegion,
  onSelectFlag,
  onExplore,
  onBackToFlags,
  includeReferenceList,
  includeBlockQuote,
  onToggleReferenceList,
  onToggleBlockQuote,
  excludedMatchesQuery,
  expandedMatchId,
  onExpandMatch,
}: {
  selectedFlagId: number | null;
  isExploring: boolean;
  flags: FlagSummaryOut[] | undefined;
  flagsPending: boolean;
  flagsError: boolean;
  selectedRegion: FlagRegionOut | null;
  onSelectFlag: (flagId: number) => void;
  onExplore: () => void;
  onBackToFlags: () => void;
  includeReferenceList: boolean;
  includeBlockQuote: boolean;
  onToggleReferenceList: (value: boolean) => void;
  onToggleBlockQuote: (value: boolean) => void;
  excludedMatchesQuery: ReturnType<typeof useExcludedReuseMatches>;
  expandedMatchId: string | null;
  onExpandMatch: (id: string | null) => void;
}) {
  if (isExploring) {
    return (
      <ReuseExplorePanel
        flags={flags}
        onSelectFlag={onSelectFlag}
        onBack={onBackToFlags}
        includeReferenceList={includeReferenceList}
        includeBlockQuote={includeBlockQuote}
        onToggleReferenceList={onToggleReferenceList}
        onToggleBlockQuote={onToggleBlockQuote}
        excludedMatchesQuery={excludedMatchesQuery}
        expandedMatchId={expandedMatchId}
        onExpandMatch={onExpandMatch}
      />
    );
  }
  if (selectedFlagId === null) {
    return (
      <BrowseFlags flags={flags} isPending={flagsPending} isError={flagsError} onSelect={onSelectFlag} onExplore={onExplore} />
    );
  }
  return (
    <>
      {selectedRegion && regionCopy(selectedRegion) && (
        <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
          {regionCopy(selectedRegion)}
        </p>
      )}
      <FlagDetailCard flagId={selectedFlagId} onBack={onBackToFlags} onExplore={onExplore} />
    </>
  );
}

export function DocumentViewerPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedFlagId = searchParams.get("flag") ? Number(searchParams.get("flag")) : null;
  // V-072 (F7.4): the exploration panel's own URL state, mutually
  // exclusive with `flag` (same "one analysis-pane view at a time"
  // convention `flag` already establishes).
  const isExploring = searchParams.get("panel") === "explore";
  const includeReferenceList = searchParams.get("ref") === "1";
  const includeBlockQuote = searchParams.get("quote") === "1";
  const expandedMatchId = searchParams.get("match");
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Manuscript - VERIDICAL", headingRef);

  const { data: viewer, isPending, isError, refetch } = useManuscriptViewer(id);
  const { data: flags, isPending: flagsPending, isError: flagsError } = useFlags(id);
  const excludedMatchesQuery = useExcludedReuseMatches(id, { includeReferenceList, includeBlockQuote });
  const isDocx = viewer?.available === true && viewer.source_format === "docx";
  const {
    data: paragraphsData,
    isPending: paragraphsPending,
    isError: paragraphsError,
    refetch: refetchParagraphs,
  } = useManuscriptParagraphs(id, isDocx);

  function selectFlag(flagId: number) {
    const next = new URLSearchParams(searchParams);
    next.set("flag", String(flagId));
    next.delete("panel");
    next.delete("match");
    setSearchParams(next);
  }
  function clearFlag() {
    const next = new URLSearchParams(searchParams);
    next.delete("flag");
    setSearchParams(next);
  }
  function openExplore() {
    const next = new URLSearchParams(searchParams);
    next.delete("flag");
    next.set("panel", "explore");
    setSearchParams(next);
  }
  function closeExplore() {
    const next = new URLSearchParams(searchParams);
    next.delete("panel");
    next.delete("ref");
    next.delete("quote");
    next.delete("match");
    setSearchParams(next);
  }
  function setToggle(key: "ref" | "quote", value: boolean) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, "1");
    else next.delete(key);
    setSearchParams(next);
  }
  function setExpandedMatch(matchId: string | null) {
    const next = new URLSearchParams(searchParams);
    if (matchId) next.set("match", matchId);
    else next.delete("match");
    setSearchParams(next);
  }

  const expandedMatch =
    excludedMatchesQuery.data?.matches.find((m) => m.id === expandedMatchId) ?? null;
  const selectedRegion = isExploring
    ? expandedMatch?.own_region ?? null
    : (viewer?.regions.find((r) => r.flag_id === selectedFlagId) ?? null);
  const requestedPage = selectedRegion?.page ?? null;
  // The explore panel's expanded match reuses PdfPane's existing
  // highlight mechanism exactly as any other flag does -- its region is
  // just appended to the same list, keyed by the synthetic negative
  // `flag_id` the backend already generated for it (never a real Flag.id).
  const pdfRegions =
    isExploring && expandedMatch ? [...(viewer?.regions ?? []), expandedMatch.own_region] : viewer?.regions ?? [];
  const pdfSelectedFlagId = isExploring ? (expandedMatch?.own_region.flag_id ?? null) : selectedFlagId;

  // Mobile Document/Analysis tabs (`ui-designer` spec 2026-08-22): AC3's
  // "activating a finding scrolls the document to it" has zero observable
  // effect on a narrow viewport unless the document pane is actually
  // visible, so selecting any region auto-switches to Document. The
  // reverse never happens automatically -- tapping a highlight inside the
  // Document tab doesn't evict the instructor from what they're reading;
  // the Analysis tab's content is already correct whenever they choose to
  // look at it next.
  const [activeTab, setActiveTab] = useState<"document" | "analysis">(
    selectedRegion ? "document" : "analysis",
  );
  useEffect(() => {
    if (selectedRegion) setActiveTab("document");
  }, [selectedRegion]);

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      <header className="flex flex-col gap-1 border-b border-border px-4 py-2.5 sm:px-6">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-ink-tertiary">
          <Link to="/dashboard" className="text-link underline hover:text-link-hover">Dashboard</Link>
          <span aria-hidden="true">/</span>
          <Link to={`/report/${id}`} className="text-link underline hover:text-link-hover">Report</Link>
          <span aria-hidden="true">/</span>
          <span>Document</span>
        </nav>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink">
          Manuscript{viewer?.original_filename ? `: ${viewer.original_filename}` : ""}
        </h1>
      </header>

      {isPending && (
        <p role="status" aria-live="polite" aria-busy="true" className="flex-1 p-6 text-sm text-ink-secondary">
          <SpinnerIcon /> Loading manuscript.
        </p>
      )}
      {isError && (
        <div role="alert" className="flex-1 p-6 text-sm text-status-attention-text">
          This manuscript couldn't be loaded.{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      )}

      {viewer && !viewer.available && (
        <div className="flex-1 p-6">
          <p className="rounded-lg bg-status-neutral-bg p-4 text-sm text-status-neutral-text">
            {viewer.unavailable_reason}
          </p>
        </div>
      )}

      {viewer && viewer.available && (viewer.source_format === "pdf" || viewer.source_format === "docx") && (
        <div className="flex min-h-0 flex-1 flex-col">
          {/* Mobile Document/Analysis tabs (`ui-designer` spec, 2026-08-22).
              Found live (`ux-critic`, 2026-08-19): at 320px a fixed
              side-by-side grid left the Next button physically
              unreachable. Below `lg` (AppShell's own nav-collapse
              breakpoint) the two panes become real tabs instead of a
              stacked column -- every control stays reachable AND
              reachable without scrolling past the other pane first. */}
          <Tabs
            label="Document view"
            className="lg:hidden"
            active={activeTab}
            onChange={(id2) => setActiveTab(id2 as "document" | "analysis")}
            tabs={[
              { id: "document", label: "Document" },
              { id: "analysis", label: "Analysis", badge: flags ? String(flags.length) : undefined },
            ]}
          />
          <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1">
            <TabPanel
              id="document"
              label="Document"
              active={activeTab === "document"}
              className="lg:block min-h-0 border-b border-border lg:border-r lg:border-b-0"
            >
              {viewer.source_format === "pdf" ? (
                <PdfPane
                  fileUrl={`${BASE_URL}/check-runs/${id}/document/file`}
                  regions={pdfRegions}
                  flags={flags ?? []}
                  selectedFlagId={pdfSelectedFlagId}
                  onSelectFlag={selectFlag}
                  requestedPage={requestedPage}
                />
              ) : (
                <DocxPane
                  paragraphs={paragraphsData?.paragraphs}
                  paragraphsPending={paragraphsPending}
                  paragraphsError={paragraphsError}
                  onRetry={() => refetchParagraphs()}
                  regions={pdfRegions}
                  flags={flags ?? []}
                  selectedFlagId={pdfSelectedFlagId}
                  onSelectFlag={selectFlag}
                  isVisible={activeTab === "document"}
                />
              )}
            </TabPanel>
            <TabPanel
              id="analysis"
              label="Analysis"
              active={activeTab === "analysis"}
              className="lg:block min-h-0 overflow-y-auto"
            >
              <AnalysisPane
                selectedFlagId={selectedFlagId}
                isExploring={isExploring}
                flags={flags}
                flagsPending={flagsPending}
                flagsError={flagsError}
                selectedRegion={selectedRegion}
                onSelectFlag={selectFlag}
                onExplore={openExplore}
                onBackToFlags={isExploring ? closeExplore : clearFlag}
                includeReferenceList={includeReferenceList}
                includeBlockQuote={includeBlockQuote}
                onToggleReferenceList={(v) => setToggle("ref", v)}
                onToggleBlockQuote={(v) => setToggle("quote", v)}
                excludedMatchesQuery={excludedMatchesQuery}
                expandedMatchId={expandedMatchId}
                onExpandMatch={setExpandedMatch}
              />
            </TabPanel>
          </div>
        </div>
      )}
    </div>
  );
}
