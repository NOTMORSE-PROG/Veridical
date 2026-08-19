// V-065 (AC1-4, 6, 7 -- AC5/F7.4 passage-pair is a follow-up ticket, see
// tickets/V8-real-use/open/V-065.md's 2026-08-19 scope-split note): the
// split-pane manuscript viewer. Document pane on the left (PDF.js for PDF
// sources, a reconstructed-text pane for DOCX), analysis pane on the
// right (the existing flags list, browse or one flag's detail).
import { useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { AnchorPill } from "../components/AnchorPill";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { StatusPill } from "../components/StatusPill";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { truncateAtWord } from "../format/text";
import { useFlag } from "../flags/useFlag";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useFlags, useManuscriptViewer } from "../report/useReport";
import type { FlagSummaryOut } from "../api/types";
import { PdfPane } from "./PdfPane";
import { regionCopy } from "./regionCopy";

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
}: {
  flags: FlagSummaryOut[] | undefined;
  isPending: boolean;
  isError: boolean;
  onSelect: (flagId: number) => void;
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
      <p className="p-4 text-sm text-ink-secondary">
        No integrity flags on this run. VERIDICAL's four integrity checks (internal agreement,
        citation integrity, statistical forensics, and originality/reuse) found nothing to report.
      </p>
    );
  }

  return (
    <div>
      <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
        Citation and originality checks skip the reference list itself when comparing text, so a
        shared bibliography entry doesn't get flagged as reused content.
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

function FlagDetailCard({ flagId, onBack }: { flagId: number; onBack: () => void }) {
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
      <Link to={`/flags/${flag.id}`} className="w-fit text-sm font-medium text-link underline hover:text-link-hover">
        Open as full page
      </Link>
    </div>
  );
}

export function DocumentViewerPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedFlagId = searchParams.get("flag") ? Number(searchParams.get("flag")) : null;
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Manuscript - VERIDICAL", headingRef);

  const { data: viewer, isPending, isError, refetch } = useManuscriptViewer(id);
  const { data: flags, isPending: flagsPending, isError: flagsError } = useFlags(id);

  function selectFlag(flagId: number) {
    const next = new URLSearchParams(searchParams);
    next.set("flag", String(flagId));
    setSearchParams(next);
  }
  function clearFlag() {
    const next = new URLSearchParams(searchParams);
    next.delete("flag");
    setSearchParams(next);
  }

  const selectedRegion = viewer?.regions.find((r) => r.flag_id === selectedFlagId) ?? null;
  const requestedPage = selectedRegion?.page ?? null;

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

      {viewer && viewer.available && viewer.source_format === "pdf" && (
        // Found live (`ux-critic`, 2026-08-19): at 320px the fixed
        // side-by-side grid left the Next button physically unreachable
        // (a real Playwright click failure, not a visual judgment -- the
        // analysis pane's own overflow container intercepted the click).
        // A single stacked column below `lg` (matching AppShell's own
        // nav-collapse breakpoint) is the minimal real fix: every control
        // stays reachable, even though the polished tab UI from the
        // ui-designer spec (§3.2) is still a named, separate gap.
        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1">
          <div className="min-h-0 border-b border-border lg:border-r lg:border-b-0">
            <PdfPane
              checkRunId={id}
              regions={viewer.regions}
              flags={flags ?? []}
              selectedFlagId={selectedFlagId}
              onSelectFlag={selectFlag}
              requestedPage={requestedPage}
            />
          </div>
          <div className="min-h-0 overflow-y-auto">
            {selectedFlagId === null ? (
              <BrowseFlags
                flags={flags}
                isPending={flagsPending}
                isError={flagsError}
                onSelect={selectFlag}
              />
            ) : (
              <>
                {selectedRegion && regionCopy(selectedRegion) && (
                  <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
                    {regionCopy(selectedRegion)}
                  </p>
                )}
                <FlagDetailCard flagId={selectedFlagId} onBack={clearFlag} />
              </>
            )}
          </div>
        </div>
      )}

      {viewer && viewer.available && viewer.source_format === "docx" && (
        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1">
          <div className="min-h-0 overflow-y-auto border-b border-border p-4 lg:border-r lg:border-b-0">
            <p className="rounded-lg bg-status-info-bg px-4 py-2.5 text-sm text-status-info-text">
              Reconstructed text. This is VERIDICAL's extracted paragraph text, not the original
              file's exact layout, tables, or images. Page numbers do not apply here; flags below
              show their paragraph number instead.
            </p>
          </div>
          <div className="min-h-0 overflow-y-auto">
            {selectedFlagId === null ? (
              <BrowseFlags
                flags={flags}
                isPending={flagsPending}
                isError={flagsError}
                onSelect={selectFlag}
              />
            ) : (
              <>
                {selectedRegion && regionCopy(selectedRegion) && (
                  <p className="border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs text-status-neutral-text">
                    {regionCopy(selectedRegion)}
                  </p>
                )}
                <FlagDetailCard flagId={selectedFlagId} onBack={clearFlag} />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
