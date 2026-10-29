import { SourceViewDetails } from "./SourceViewDetails";
// V-065 AC1 (DOCX gap), design review spec (2026-08-22): the reconstructed-
// text pane's interactive half. Mirrors PdfPane's mechanism exactly (same
// hover-preview/click-select/keyboard/Escape-dismiss/focus-on-select
// behavior, same highlight token) so the two panes read as parallel
// implementations of one idea, not two diverging ones -- DOCX has no page
// concept, so "jump to the right page" collapses to "scroll to the right
// paragraph," which needs no render race to guard against at all.
import { useId, useEffect, useRef, useState } from "react";
import type { DocumentImageOut, DocumentParagraphOut, DocumentTableOut, FlagRegionOut, FlagSummaryOut, NativeMathSourceOut } from "../api/types";
import { NativeMathPieces, NativeMathInventoryNote, nativeMathInventoryLabel } from "./NativeMathSource";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { truncateAtWord } from "../format/text";
import { DocxSourceMedia } from "./DocxSourceMedia";
import { COMPARISON_LEAD_COPY, comparisonLeadIdentity, isOptionalComparisonLead, sourceRecordPresentation, type ComparisonLeadSnapshot } from "../domain/comparisonLeadIdentity";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { recordSourceReadingPlacement, useDocxReadingPosition } from "./useDocxReadingPosition";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

const EMPTY_FINDING_NUMBERS: ReadonlyMap<number, number> = new Map();
const EMPTY_TABLES: DocumentTableOut[] = [];
const EMPTY_IMAGES: DocumentImageOut[] = [];

export function DocxPane({
  paragraphs,
  paragraphsPending,
  paragraphsError,
  onRetry,
  regions,
  flags,
  selectedFlagId,
  onSelectFlag,
  isVisible,
  focusSelection = true,
  findingNumbers = EMPTY_FINDING_NUMBERS,
  comparisonLeads,
  requestedParagraph = null,
  tables = EMPTY_TABLES,
  images = EMPTY_IMAGES,
  omittedImageOccurrences = 0,
  nativeMath,
  imageBasePath,
  onPlaceRequestedParagraph,
  requestedParagraphAttempt,
  scrollInParent = false,
  documentKind = "manuscript",
  sourceQuery,
  loadImage,
}: {
  paragraphs: DocumentParagraphOut[] | undefined;
  paragraphsPending: boolean;
  paragraphsError: boolean;
  onRetry: () => void;
  regions: FlagRegionOut[];
  flags: FlagSummaryOut[];
  selectedFlagId: number | null;
  onSelectFlag: (flagId: number) => void;
  // UX review finding (2026-08-22): on mobile, selecting a flag from the
  // Analysis tab switches this pane's TabPanel from `display:none` to
  // visible in the SAME commit the focus-on-select effect below wants to
  // run in -- a browser refuses to move focus into a still-hidden
  // subtree, so the call silently no-ops with no retry (PdfPane dodges
  // this by accident: its own effect is keyed on `boxes`, which happens
  // to recompute once the canvas actually lays out post-visibility).
  // Passed down so the effect can retry once the pane genuinely becomes
  // visible, not just once per selection.
  isVisible: boolean;
  focusSelection?: boolean;
  findingNumbers?: ReadonlyMap<number, number>;
  comparisonLeads?: ComparisonLeadSnapshot;
  requestedParagraph?: number | null;
  tables?: DocumentTableOut[];
  images?: DocumentImageOut[];
  omittedImageOccurrences?: number;
  nativeMath?: NativeMathSourceOut | null;
  imageBasePath?: string;
  onPlaceRequestedParagraph?: (element: HTMLElement) => void;
  requestedParagraphAttempt?: number;
  scrollInParent?: boolean;
  documentKind?: "manuscript" | "required_format";
  sourceQuery?: string;
  loadImage?: (path: string) => Promise<Blob>;
}) {
  const formatDocument = documentKind === "required_format";
  const documentLabel = formatDocument ? "Original format" : "Full manuscript";
  const detailsId = useId();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const comparisonMarkers = regions.some((region) => flags.some((flag) => flag.id === region.flag_id && isOptionalComparisonLead(flag)));
  const [hoveredFlagId, setHoveredFlagId] = useState<number | null>(null);
  const highlightRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const pendingSelectionFocus = useRef<FocusContinuation | null>(null);
  const observedSelection = useRef<number | null | undefined>(undefined);
  const paperRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  useDocxReadingPosition(scrollRef, isVisible, !scrollInParent);
  const paragraphPlacement = useRef<{ pending: boolean; ownership: FocusContinuation } | null>(null);

  useEffect(() => {
    paragraphPlacement.current?.ownership.dispose();
    paragraphPlacement.current = requestedParagraph !== null && Number.isSafeInteger(requestedParagraph)
      ? { pending: true, ownership: claimFocusContinuation() } : null;
    return () => { paragraphPlacement.current?.ownership.dispose(); paragraphPlacement.current = null; };
  }, [requestedParagraph, requestedParagraphAttempt]);

  useEffect(() => {
    const placement = paragraphPlacement.current;
    if (!placement?.pending || requestedParagraph === null || !isVisible) return;
    const target = paperRef.current?.querySelector<HTMLElement>(`[data-source-paragraph="${requestedParagraph}"]`);
    if (!target) return;
    placement.pending = false;
    if (onPlaceRequestedParagraph) {
      placement.ownership.dispose();
      onPlaceRequestedParagraph(target);
    } else placement.ownership.focus(target);
    if (scrollRef.current) recordSourceReadingPlacement(scrollRef.current);
  }, [requestedParagraph, paragraphs, tables, images, nativeMath, isVisible, onPlaceRequestedParagraph, requestedParagraphAttempt]);

  const flagsById = new Map(flags.map((f) => [f.id, f]));

  // One highlighted paragraph per real flag whose region resolved to
  // `paragraph_only` -- multiple flags can share a paragraph (a citation
  // flag and a reuse flag both anchored to the same ¶N is a real,
  // plausible case), grouped here so the paragraph renders exactly one
  // highlight regardless of how many findings point at it.
  const flagIdsByParagraph = new Map<number, number[]>();
  for (const region of regions) {
    if (region.kind !== "paragraph_only" || region.paragraph === null) continue;
    const list = flagIdsByParagraph.get(region.paragraph) ?? [];
    list.push(region.flag_id);
    flagIdsByParagraph.set(region.paragraph, list);
  }

  // Same fix UX review already shipped in PdfPane (2026-08-19): in
  // detail mode, only the SELECTED flag's own paragraph highlights --
  // otherwise an unrelated flag's box could be the only one visible on a
  // paragraph the instructor never asked about, with nothing marking it
  // "not yours."
  function visibleFlagIdsFor(paragraphFlagIds: number[]): number[] {
    if (selectedFlagId === null) return paragraphFlagIds;
    return paragraphFlagIds.includes(selectedFlagId) ? [selectedFlagId] : [];
  }

  useEffect(() => {
    pendingSelectionFocus.current?.dispose();
    const changed = observedSelection.current !== selectedFlagId;
    observedSelection.current = selectedFlagId;
    pendingSelectionFocus.current = changed && selectedFlagId !== null && isVisible && focusSelection ? claimFocusContinuation() : null;
    return () => { pendingSelectionFocus.current?.dispose(); pendingSelectionFocus.current = null; };
  }, [selectedFlagId, isVisible, focusSelection]);

  useEffect(() => {
    if (selectedFlagId === null || !isVisible || !focusSelection) return;
    const el = highlightRefs.current.get(selectedFlagId);
    if (!el) return;
    pendingSelectionFocus.current?.focus(el);
    pendingSelectionFocus.current = null;
  }, [selectedFlagId, paragraphs, tables, images, isVisible, focusSelection]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setHoveredFlagId(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const popoverFlag = hoveredFlagId !== null && hoveredFlagId !== selectedFlagId
    ? flagsById.get(hoveredFlagId)
    : null;

  // A selected flag whose region is `paragraph_only` but whose paragraph
  // index isn't in the fetched list at all -- e.g. the parser skipped an
  // empty body item the anchor still counted. Real, honest, never silent.
  const selectedRegion =
    selectedFlagId !== null ? regions.find((r) => r.flag_id === selectedFlagId) : undefined;
  const selectedParagraphMissing =
    selectedRegion?.kind === "paragraph_only" &&
    selectedRegion.paragraph !== null &&
    paragraphs !== undefined &&
    !paragraphs.some((p) => p.paragraph === selectedRegion.paragraph) &&
    !tables.some((t) => t.paragraph === selectedRegion.paragraph) &&
    !images.some((image) => image.paragraph === selectedRegion.paragraph) &&
    !nativeMath?.containers.some((container) => container.paragraph === selectedRegion.paragraph);
  const bodyPositions = [...new Set([
    ...(paragraphs ?? []).map((p) => p.paragraph),
    ...tables.map((table) => table.paragraph),
    ...images.map((image) => image.paragraph),
    ...(nativeMath?.containers.flatMap((container) => container.paragraph === null ? [] : [container.paragraph]) ?? []),
  ])].sort((a, b) => a - b);

  return (
    <div className="signal-document-source">
      <div className="signal-document-toolbar">
        <div className="signal-document-toolbar__identity">
          <strong>{documentLabel}</strong>
          <span>Reconstructed DOCX. Layout may differ.{comparisonMarkers && " · L markers are comparison leads (not scored)"}</span>
        </div>
        <div className="signal-document-toolbar__controls"><button type="button" aria-expanded={detailsOpen} aria-controls={detailsId} onClick={() => setDetailsOpen((value) => !value)}>Source view details</button></div>
      </div>

      <div ref={scrollRef} data-reading-position={scrollInParent ? undefined : "managed"} role={scrollInParent ? undefined : "region"} tabIndex={scrollInParent ? undefined : 0} aria-label={scrollInParent ? undefined : `${documentLabel}, scrollable`} className="signal-document-scroll signal-document-scroll--docx">
        <SourceViewDetails id={detailsId} open={detailsOpen} format="docx" comparisonMarkers={comparisonMarkers} freshFormatView={formatDocument}>
          {formatDocument && !paragraphsError && paragraphs && <p>{paragraphs.length} paragraphs · {tables.length} tables · {images.length} drawing occurrences. {nativeMathInventoryLabel(nativeMath)} · Main document body.</p>}
        </SourceViewDetails>
        <article ref={paperRef} className="signal-document-docx-paper" aria-label={formatDocument ? "Required format reading view" : "Reconstructed manuscript document"}>
          {!formatDocument && <header className="signal-document-docx-paper__header">
            <span>Reconstructed manuscript</span>
            <span>{paragraphsError ? "Reading view unavailable" : paragraphs ? `${paragraphs.length} paragraphs · ${tables.length} tables · ${images.length} drawing occurrences` : "Loading reading view"}</span>
            {paragraphs && <span>{nativeMathInventoryLabel(nativeMath)} · Main document body</span>}
          </header>}
          <div className="signal-document-docx-paper__body">
        {paragraphsError && (
          <p role="alert" className="p-4 text-sm text-status-attention-text">
            {formatDocument ? "The format reading view could not be opened." : "This manuscript's text couldn't be loaded."}{" "}
            <button type="button" onClick={onRetry} className="font-medium underline">
              Try again
            </button>
            .
          </p>
        )}
        {paragraphsPending && !paragraphsError && (
          <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
            <SpinnerIcon /> {formatDocument ? "Loading original format." : "Loading manuscript text."}
          </p>
        )}
        {!paragraphsPending && !paragraphsError && paragraphs && bodyPositions.length === 0 && (
          <p className="p-4 text-sm text-ink-secondary">
            VERIDICAL could not extract any readable text from this file.
          </p>
        )}
        {omittedImageOccurrences > 0 && <p role="status" className="signal-document-reconstruction-note">
          {omittedImageOccurrences} additional drawing occurrences exceed the source-view limit and are not displayed or assessed here.
        </p>}
        {!paragraphsPending && !paragraphsError && <NativeMathInventoryNote source={nativeMath} readingOnly={formatDocument} />}
        {nativeMath?.containers.some((container) => container.paragraph === null) &&
          <p className="signal-document-reconstruction-note">Native Word equations were recorded, but their reconstructed source locations are unavailable. Check the original DOCX for their formulas and layout.</p>}
        {selectedParagraphMissing && (
          <p className="mb-3 rounded-lg bg-status-neutral-bg px-4 py-2.5 text-xs text-status-neutral-text">
            VERIDICAL could not locate the recorded paragraph in the reconstructed
            text below. The evidence panel on the right is everything VERIDICAL recorded for this
            flag.
          </p>
        )}
        {!paragraphsPending &&
          !paragraphsError &&
          paragraphs &&
          bodyPositions.map((position) => {
            const p = paragraphs.find((paragraph) => paragraph.paragraph === position);
            const sourceTables = tables.filter((table) => table.paragraph === position);
            const sourceImages = images.filter((image) => image.paragraph === position);
            const nativeContainers = nativeMath?.containers.filter((container) => container.paragraph === position) ?? [];
            const tableOccurrences = new Set(sourceTables.flatMap(table => table.native_cells?.flatMap(cell =>
              cell.pieces.flatMap(piece => piece.kind === "native_math" && piece.occurrence !== null ? [piece.occurrence] : [])) ?? []));
            const gapContainers = nativeContainers.filter(container => container.kind === "paragraph"
              || container.pieces.some(piece => piece.kind === "native_math" && (piece.occurrence === null || !tableOccurrences.has(piece.occurrence))));
            const renderText = () => {
              if (!p) return null;
            // Found live (report/44, real data): F7 reuse flags can anchor
            // to a CHAPTER-HEADING paragraph's own index, not just a body
            // paragraph -- the chunker embeds a passage starting at the
            // heading. An early return for headings before checking for a
            // flag would silently make every heading-anchored flag
            // unreachable from the document pane, the exact "nothing
            // signals this is clickable" failure this pane exists to fix.
            const paragraphFlagIds = visibleFlagIdsFor(flagIdsByParagraph.get(p.paragraph) ?? []);
            const primaryFlagId = paragraphFlagIds[0];
            const primaryFlag = primaryFlagId !== undefined ? flagsById.get(primaryFlagId) : undefined;
            const HeadingTag =
              p.heading_level !== null
                ? (`h${Math.min(1 + p.heading_level, 6)}` as keyof React.JSX.IntrinsicElements)
                : null;

            if (!primaryFlag) {
              return HeadingTag ? (
                <HeadingTag key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mt-3 mb-1.5 text-md font-semibold text-ink">
                  {p.text}
                </HeadingTag>
              ) : (
                <p key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mb-2 text-sm text-ink">
                  {p.text}
                </p>
              );
            }

            const textClassName = HeadingTag
              ? "text-left text-md font-semibold text-ink"
              : "text-left text-sm text-ink";
            const primaryIdentity = sourceRecordPresentation(primaryFlag, findingNumbers.get(primaryFlagId) ?? 1, comparisonLeads);

            // UX review finding (2026-08-22): a single button whose
            // aria-label claimed "2 findings" but whose onClick always
            // resolved to the same first flag made every co-located
            // finding past the first structurally unreachable -- not just
            // hard to find, impossible to reach, by click OR keyboard.
            // One flag keeps the single always-reachable highlight;
            // multiple flags get one numbered badge each (mirrors
            // PdfPane's own `ordinal` concept for overlapping boxes),
            // every badge independently focusable/clickable/registered.
            if (paragraphFlagIds.length === 1) {
              const highlightButton = (
                <button
                  type="button"
                  data-source-record-id={primaryFlagId}
                  ref={(el) => {
                    if (el) highlightRefs.current.set(primaryFlagId, el);
                    else highlightRefs.current.delete(primaryFlagId);
                  }}
                  aria-label={`${primaryIdentity.label}: ${CHECK_KIND_SHORT_LABEL[primaryFlag.check_kind] ?? primaryFlag.check_kind}. ${primaryIdentity.qualification}. Extracted paragraph ${p.paragraph + 1}. Paragraph location.${primaryFlagId === selectedFlagId ? " Selected." : ""}`}
                  aria-pressed={primaryFlagId === selectedFlagId}
                  aria-describedby={popoverFlag?.id === primaryFlagId ? `docx-region-popover-${primaryFlagId}` : undefined}
                  onMouseEnter={() => setHoveredFlagId(primaryFlagId)}
                  onMouseLeave={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onFocus={() => setHoveredFlagId(primaryFlagId)}
                  onBlur={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onClick={() => onSelectFlag(primaryFlagId)}
                  className={`signal-document-docx-location${primaryFlagId === selectedFlagId ? " is-selected" : ""} ${textClassName}`}
                >
                  <span className={`signal-document-marker signal-document-marker--inline${isOptionalComparisonLead(primaryFlag) ? " signal-document-marker--comparison" : ""}`} aria-hidden="true">{primaryIdentity.marker}</span>
                  <span>{p.text}</span>
                </button>
              );
              return HeadingTag ? (
                <HeadingTag key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mt-3 mb-1.5">
                  {highlightButton}
                </HeadingTag>
              ) : (
                <div key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mb-2">
                  {highlightButton}
                </div>
              );
            }

            const multiFlagBlock = (
              <div className="signal-document-docx-location signal-document-docx-location--multiple">
                <p className={textClassName}>{p.text}</p>
                <div className="mt-1.5 flex flex-wrap gap-1" role="group" aria-label={`${paragraphFlagIds.length} ${paragraphFlagIds.some((flagId) => { const flag = flagsById.get(flagId); return flag && isOptionalComparisonLead(flag); }) ? "recorded locations" : "findings"} in this paragraph`}>
                  {paragraphFlagIds.map((flagId, i) => {
                    const flag = flagsById.get(flagId);
                    if (!flag) return null;
                    const identity = sourceRecordPresentation(flag, findingNumbers.get(flagId) ?? i + 1, comparisonLeads);
                    return (
                      <button
                        key={flagId}
                        type="button"
                        data-source-record-id={flagId}
                        ref={(el) => {
                          if (el) highlightRefs.current.set(flagId, el);
                          else highlightRefs.current.delete(flagId);
                        }}
                        aria-label={`${identity.label}: ${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}. ${identity.qualification}. Extracted paragraph ${p.paragraph + 1}. Paragraph location.${flagId === selectedFlagId ? " Selected." : ""}`}
                        aria-pressed={flagId === selectedFlagId}
                        aria-describedby={popoverFlag?.id === flagId ? `docx-region-popover-${flagId}` : undefined}
                        onMouseEnter={() => setHoveredFlagId(flagId)}
                        onMouseLeave={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onFocus={() => setHoveredFlagId(flagId)}
                        onBlur={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onClick={() => onSelectFlag(flagId)}
                        className={`signal-document-co-located-marker${flagId === selectedFlagId ? " is-selected" : ""}`}
                      >
                        {identity.marker}
                      </button>
                    );
                  })}
                </div>
              </div>
            );

            return HeadingTag ? (
              <HeadingTag key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mt-3 mb-1.5">
                {multiFlagBlock}
              </HeadingTag>
            ) : (
              <div key={p.paragraph} data-source-paragraph={p.paragraph} tabIndex={-1} className="mb-2">
                {multiFlagBlock}
              </div>
            );
            };
            const mediaFlags = !p ? visibleFlagIdsFor(flagIdsByParagraph.get(position) ?? []) : [];
            return <section key={position} className="signal-docx-source-item"
              data-source-paragraph={!p ? position : undefined} tabIndex={!p ? -1 : undefined}>
              {renderText()}
              {gapContainers.length > 0 && <div className="signal-document-reconstruction-note">
                {gapContainers.map((container, index) => <p key={index}>
                  {!container.pieces.some(piece => piece.kind === "native_math") && <strong>Native Word equation not displayed</strong>}
                  <NativeMathPieces pieces={container.pieces} />
                </p>)}
              </div>}
              {mediaFlags.length > 0 && <div className="signal-docx-source-findings" role="group" aria-label="Findings at this source location">
                {mediaFlags.map((flagId) => <button key={flagId} type="button" data-source-record-id={flagId}
                  ref={(el) => { if (el) highlightRefs.current.set(flagId, el); else highlightRefs.current.delete(flagId); }}
                  className={`signal-document-co-located-marker${flagId === selectedFlagId ? " is-selected" : ""}`}
                  aria-pressed={flagId === selectedFlagId} onClick={() => onSelectFlag(flagId)}>
                  {flagsById.has(flagId) ? sourceRecordPresentation(flagsById.get(flagId)!, findingNumbers.get(flagId) ?? 1, comparisonLeads).label : `Finding ${findingNumbers.get(flagId) ?? 1}`} · source location only
                </button>)}
              </div>}
              <DocxSourceMedia tables={sourceTables} images={sourceImages} imageBasePath={imageBasePath} sourceQuery={sourceQuery} loadImage={loadImage} isVisible={isVisible} readingOnly={formatDocument} />
            </section>;
          })}
          </div>
          {!paragraphsPending && !paragraphsError && paragraphs && bodyPositions.length > 0 && (
            <footer className="signal-document-docx-paper__footer">End of reconstructed manuscript</footer>
          )}
        </article>
      </div>
      {popoverFlag && (
        (() => {
          const rect = highlightRefs.current.get(popoverFlag.id)?.getBoundingClientRect();
          // first-time-user review finding (2026-08-22): the popover used to anchor
          // BELOW the trigger (`top: rect.bottom + 6`) -- fine for
          // PdfPane's small highlight boxes, but DOCX highlights are
          // full-width paragraph/badge rows with real body text directly
          // beneath them, so the popover rendered on top of the exact
          // sentence the instructor was trying to read. Anchored from the
          // BOTTOM instead (grows upward into the paragraph's own top
          // margin, never over content below it); `left` clamped so a
          // 320px-wide popover near the right edge doesn't overflow a
          // narrow mobile viewport.
          const left = Math.min(rect?.left ?? 0, window.innerWidth - 280);
          const bottom = window.innerHeight - (rect?.top ?? 0) + 6;
          return (
            <div
              id={`docx-region-popover-${popoverFlag.id}`}
              role="tooltip"
              className="pointer-events-none fixed z-raised max-w-xs rounded-md border border-border bg-panel p-2.5 text-xs shadow-sm"
              style={{ left, bottom }}
            >
              <p className="font-semibold tracking-header text-ink-tertiary uppercase">
                {CHECK_KIND_SHORT_LABEL[popoverFlag.check_kind] ?? popoverFlag.check_kind}
              </p>
              <p className="mt-1 text-ink">{truncateAtWord(popoverFlag.evidence_excerpt, 140)}</p>
              {isOptionalComparisonLead(popoverFlag) && <p>{comparisonLeadIdentity(popoverFlag, comparisonLeads).label}</p>}
              <div className="mt-1.5">
                {isOptionalComparisonLead(popoverFlag) ? <span>{COMPARISON_LEAD_COPY.badge}</span> : <SeverityTag severity={popoverFlag.severity as Severity} />}
              </div>
            </div>
          );
        })()
      )}
    </div>
  );
}
