// V-065 AC1 (DOCX gap), `ui-designer` spec (2026-08-22): the reconstructed-
// text pane's interactive half. Mirrors PdfPane's mechanism exactly (same
// hover-preview/click-select/keyboard/Escape-dismiss/focus-on-select
// behavior, same highlight token) so the two panes read as parallel
// implementations of one idea, not two diverging ones -- DOCX has no page
// concept, so "jump to the right page" collapses to "scroll to the right
// paragraph," which needs no render race to guard against at all.
import { useEffect, useRef, useState } from "react";
import type { DocumentParagraphOut, FlagRegionOut, FlagSummaryOut } from "../api/types";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { severityLabel } from "../domain/severity";
import { truncateAtWord } from "../format/text";
import { regionPrecision } from "./regionCopy";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

const EMPTY_FINDING_NUMBERS: ReadonlyMap<number, number> = new Map();

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
  findingNumbers = EMPTY_FINDING_NUMBERS,
}: {
  paragraphs: DocumentParagraphOut[] | undefined;
  paragraphsPending: boolean;
  paragraphsError: boolean;
  onRetry: () => void;
  regions: FlagRegionOut[];
  flags: FlagSummaryOut[];
  selectedFlagId: number | null;
  onSelectFlag: (flagId: number) => void;
  // `ux-critic` finding (2026-08-22): on mobile, selecting a flag from the
  // Analysis tab switches this pane's TabPanel from `display:none` to
  // visible in the SAME commit the focus-on-select effect below wants to
  // run in -- a browser refuses to move focus into a still-hidden
  // subtree, so the call silently no-ops with no retry (PdfPane dodges
  // this by accident: its own effect is keyed on `boxes`, which happens
  // to recompute once the canvas actually lays out post-visibility).
  // Passed down so the effect can retry once the pane genuinely becomes
  // visible, not just once per selection.
  isVisible: boolean;
  findingNumbers?: ReadonlyMap<number, number>;
}) {
  const [hoveredFlagId, setHoveredFlagId] = useState<number | null>(null);
  const highlightRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

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

  // Same fix `ux-critic` already shipped in PdfPane (2026-08-19): in
  // detail mode, only the SELECTED flag's own paragraph highlights --
  // otherwise an unrelated flag's box could be the only one visible on a
  // paragraph the instructor never asked about, with nothing marking it
  // "not yours."
  function visibleFlagIdsFor(paragraphFlagIds: number[]): number[] {
    if (selectedFlagId === null) return paragraphFlagIds;
    return paragraphFlagIds.includes(selectedFlagId) ? [selectedFlagId] : [];
  }

  useEffect(() => {
    if (selectedFlagId === null || !isVisible) return;
    const el = highlightRefs.current.get(selectedFlagId);
    el?.focus({ preventScroll: false });
  }, [selectedFlagId, paragraphs, isVisible]);

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
  const scopeRegions = regions.filter((region) => region.kind !== "paragraph_only");

  // A selected flag whose region is `paragraph_only` but whose paragraph
  // index isn't in the fetched list at all -- e.g. the parser skipped an
  // empty body item the anchor still counted. Real, honest, never silent.
  const selectedRegion =
    selectedFlagId !== null ? regions.find((r) => r.flag_id === selectedFlagId) : undefined;
  const selectedParagraphMissing =
    selectedRegion?.kind === "paragraph_only" &&
    selectedRegion.paragraph !== null &&
    paragraphs !== undefined &&
    !paragraphs.some((p) => p.paragraph === selectedRegion.paragraph);

  return (
    <div className="signal-document-source">
      <div className="signal-document-toolbar">
        <div className="signal-document-toolbar__identity">
          <strong>Full manuscript</strong>
          <span>DOCX · Reconstructed text</span>
        </div>
      </div>
      <div className="signal-document-reconstruction-note">
        <p>
          This view preserves extracted text order and headings. Page layout, tables, and images
          may differ from the uploaded file.
        </p>
      </div>
      {scopeRegions.length > 0 && (
        <div className="signal-document-scope-markers" role="group" aria-label="Findings without an exact paragraph location">
          {scopeRegions.map((region) => {
            const flag = flagsById.get(region.flag_id);
            const number = findingNumbers.get(region.flag_id) ?? 1;
            const precision = regionPrecision(region);
            return (
              <button key={region.flag_id} type="button" aria-pressed={region.flag_id === selectedFlagId} onClick={() => onSelectFlag(region.flag_id)}>
                <span aria-hidden="true">{number}</span>
                <span>Finding {number} · {precision.label}{flag ? ` · ${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}` : ""}</span>
              </button>
            );
          })}
        </div>
      )}
      <div role="region" tabIndex={0} aria-label="Full manuscript, scrollable" className="signal-document-scroll signal-document-scroll--docx">
        <article className="signal-document-docx-paper" aria-label="Reconstructed manuscript text">
          <header className="signal-document-docx-paper__header">
            <span>Reconstructed manuscript</span>
            <span>{paragraphs ? `${paragraphs.length} extracted ${paragraphs.length === 1 ? "paragraph" : "paragraphs"}` : "Extracting text"}</span>
          </header>
          <div className="signal-document-docx-paper__body">
        {paragraphsError && (
          <p role="alert" className="p-4 text-sm text-status-attention-text">
            This manuscript's text couldn't be loaded.{" "}
            <button type="button" onClick={onRetry} className="font-medium underline">
              Try again
            </button>
            .
          </p>
        )}
        {paragraphsPending && !paragraphsError && (
          <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
            <SpinnerIcon /> Loading manuscript text.
          </p>
        )}
        {!paragraphsPending && !paragraphsError && paragraphs && paragraphs.length === 0 && (
          <p className="p-4 text-sm text-ink-secondary">
            VERIDICAL could not extract any readable text from this file.
          </p>
        )}
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
          paragraphs.length > 0 &&
          paragraphs.map((p) => {
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
                <HeadingTag key={p.paragraph} className="mt-3 mb-1.5 text-md font-semibold text-ink">
                  {p.text}
                </HeadingTag>
              ) : (
                <p key={p.paragraph} className="mb-2 text-sm text-ink">
                  {p.text}
                </p>
              );
            }

            const textClassName = HeadingTag
              ? "text-left text-md font-semibold text-ink"
              : "text-left text-sm text-ink";

            // `ux-critic` finding (2026-08-22): a single button whose
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
                  ref={(el) => {
                    if (el) highlightRefs.current.set(primaryFlagId, el);
                    else highlightRefs.current.delete(primaryFlagId);
                  }}
                  aria-label={`Finding ${findingNumbers.get(primaryFlagId) ?? 1}: ${CHECK_KIND_SHORT_LABEL[primaryFlag.check_kind] ?? primaryFlag.check_kind}. ${severityLabel(primaryFlag.severity)}. Extracted paragraph ${p.paragraph + 1}. Paragraph location.${primaryFlagId === selectedFlagId ? " Selected." : ""}`}
                  aria-pressed={primaryFlagId === selectedFlagId}
                  aria-describedby={popoverFlag?.id === primaryFlagId ? `docx-region-popover-${primaryFlagId}` : undefined}
                  onMouseEnter={() => setHoveredFlagId(primaryFlagId)}
                  onMouseLeave={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onFocus={() => setHoveredFlagId(primaryFlagId)}
                  onBlur={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onClick={() => onSelectFlag(primaryFlagId)}
                  className={`signal-document-docx-location${primaryFlagId === selectedFlagId ? " is-selected" : ""} ${textClassName}`}
                >
                  <span className="signal-document-marker signal-document-marker--inline" aria-hidden="true">{findingNumbers.get(primaryFlagId) ?? 1}</span>
                  <span>{p.text}</span>
                </button>
              );
              return HeadingTag ? (
                <HeadingTag key={p.paragraph} className="mt-3 mb-1.5">
                  {highlightButton}
                </HeadingTag>
              ) : (
                <div key={p.paragraph} className="mb-2">
                  {highlightButton}
                </div>
              );
            }

            const multiFlagBlock = (
              <div className="signal-document-docx-location signal-document-docx-location--multiple">
                <p className={textClassName}>{p.text}</p>
                <div className="mt-1.5 flex flex-wrap gap-1" role="group" aria-label={`${paragraphFlagIds.length} findings in this paragraph`}>
                  {paragraphFlagIds.map((flagId, i) => {
                    const flag = flagsById.get(flagId);
                    if (!flag) return null;
                    return (
                      <button
                        key={flagId}
                        type="button"
                        ref={(el) => {
                          if (el) highlightRefs.current.set(flagId, el);
                          else highlightRefs.current.delete(flagId);
                        }}
                        aria-label={`Finding ${findingNumbers.get(flagId) ?? i + 1}: ${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}. ${severityLabel(flag.severity)}. Extracted paragraph ${p.paragraph + 1}. Paragraph location.${flagId === selectedFlagId ? " Selected." : ""}`}
                        aria-pressed={flagId === selectedFlagId}
                        aria-describedby={popoverFlag?.id === flagId ? `docx-region-popover-${flagId}` : undefined}
                        onMouseEnter={() => setHoveredFlagId(flagId)}
                        onMouseLeave={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onFocus={() => setHoveredFlagId(flagId)}
                        onBlur={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onClick={() => onSelectFlag(flagId)}
                        className={`signal-document-co-located-marker${flagId === selectedFlagId ? " is-selected" : ""}`}
                      >
                        {findingNumbers.get(flagId) ?? i + 1}
                      </button>
                    );
                  })}
                </div>
              </div>
            );

            return HeadingTag ? (
              <HeadingTag key={p.paragraph} className="mt-3 mb-1.5">
                {multiFlagBlock}
              </HeadingTag>
            ) : (
              <div key={p.paragraph} className="mb-2">
                {multiFlagBlock}
              </div>
            );
          })}
          </div>
          {!paragraphsPending && !paragraphsError && paragraphs && paragraphs.length > 0 && (
            <footer className="signal-document-docx-paper__footer">End of reconstructed manuscript</footer>
          )}
        </article>
      </div>
      {popoverFlag && (
        (() => {
          const rect = highlightRefs.current.get(popoverFlag.id)?.getBoundingClientRect();
          // `newcomer` finding (2026-08-22): the popover used to anchor
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
              <div className="mt-1.5">
                <SeverityTag severity={popoverFlag.severity as Severity} />
              </div>
            </div>
          );
        })()
      )}
    </div>
  );
}
