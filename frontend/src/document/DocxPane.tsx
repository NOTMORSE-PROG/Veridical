// V-065 AC1 (DOCX gap), `ui-designer` spec (2026-08-22): the reconstructed-
// text pane's interactive half. Mirrors PdfPane's mechanism exactly (same
// hover-preview/click-select/keyboard/Escape-dismiss/focus-on-select
// behavior, same highlight token) so the two panes read as parallel
// implementations of one idea, not two diverging ones -- DOCX has no page
// concept, so "jump to the right page" collapses to "scroll to the right
// paragraph," which needs no render race to guard against at all.
import { useEffect, useRef, useState } from "react";
import type { DocumentParagraphOut, FlagRegionOut, FlagSummaryOut } from "../api/types";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { truncateAtWord } from "../format/text";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

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

  const popoverFlag = hoveredFlagId !== null ? flagsById.get(hoveredFlagId) : null;

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
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border bg-panel px-3 py-2 text-sm">
        <p className="text-ink-secondary">
          Reconstructed text. This is VERIDICAL's extracted paragraph text, not the original
          file's exact layout, tables, or images. Select a highlighted paragraph or its numbered
          finding to see the finding.
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto bg-page p-4">
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
            VERIDICAL could not locate paragraph {selectedRegion?.paragraph} in the reconstructed
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
                  aria-label={`${CHECK_KIND_SHORT_LABEL[primaryFlag.check_kind] ?? primaryFlag.check_kind} flag, ${primaryFlag.severity} severity, paragraph ${p.paragraph}`}
                  aria-describedby={`docx-region-popover-${primaryFlagId}`}
                  onMouseEnter={() => setHoveredFlagId(primaryFlagId)}
                  onMouseLeave={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onFocus={() => setHoveredFlagId(primaryFlagId)}
                  onBlur={() => setHoveredFlagId((id) => (id === primaryFlagId ? null : id))}
                  onClick={() => onSelectFlag(primaryFlagId)}
                  className={`block w-full rounded-md px-2 py-1.5 ${textClassName}`}
                  style={{
                    background: "color-mix(in srgb, var(--color-status-caution-text) 20%, transparent)",
                    border: `${primaryFlagId === selectedFlagId ? 3 : 2}px solid var(--color-status-caution-text)`,
                  }}
                >
                  {p.text}
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
              <div
                className="rounded-md px-2 py-1.5"
                style={{
                  background: "color-mix(in srgb, var(--color-status-caution-text) 20%, transparent)",
                  border: "2px solid var(--color-status-caution-text)",
                }}
              >
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
                        aria-label={`Finding ${i + 1} of ${paragraphFlagIds.length}: ${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}, ${flag.severity} severity`}
                        aria-describedby={`docx-region-popover-${flagId}`}
                        onMouseEnter={() => setHoveredFlagId(flagId)}
                        onMouseLeave={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onFocus={() => setHoveredFlagId(flagId)}
                        onBlur={() => setHoveredFlagId((id) => (id === flagId ? null : id))}
                        onClick={() => onSelectFlag(flagId)}
                        className="flex min-h-6 min-w-6 items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold text-ink"
                        style={{
                          background: "var(--color-panel)",
                          border: `${flagId === selectedFlagId ? 3 : 1}px solid var(--color-status-caution-text)`,
                        }}
                      >
                        {i + 1}
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
