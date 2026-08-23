// V-065 AC1-4: PDF.js pane -- renders the ACTUAL submitted file in the
// browser (V-065.md Q1, owner-approved 2026-08-19: server-side
// rasterization was ruled out on measured RAM headroom, so this pane
// fetches raw bytes and pdf.js does every render). Single page at a time,
// not continuous scroll (ui-designer spec §4.1): every entry into this
// screen is anchor-driven, so "jump to the right page" is the real task,
// and N eagerly-rendered canvases is real client memory this session
// didn't measure.
import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import type { FlagRegionOut, FlagSummaryOut } from "../api/types";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { truncateAtWord } from "../format/text";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface HighlightBox {
  flagId: number;
  left: number;
  top: number;
  width: number;
  height: number;
  selected: boolean;
  ordinal: number;
}

export function PdfPane({
  fileUrl,
  regions,
  flags,
  selectedFlagId,
  onSelectFlag,
  requestedPage,
}: {
  // V-066: was `checkRunId: number` (built the URL internally, always
  // `/check-runs/{id}/document/file`) -- generalized to the URL itself so
  // the library's own-manuscript two-up can feed this the SAME component
  // pointed at `/library/{id}/document/file` instead of forking a second
  // PDF pane (Pre-implementation research Q3, `tickets/V8-real-use/open/
  // V-066.md`). Callers own building the right URL for their own route.
  fileUrl: string;
  regions: FlagRegionOut[];
  flags: FlagSummaryOut[];
  selectedFlagId: number | null;
  onSelectFlag: (flagId: number) => void;
  requestedPage: number | null;
}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [viewport, setViewport] = useState<ReturnType<PDFPageProxy["getViewport"]> | null>(null);
  const [boxes, setBoxes] = useState<HighlightBox[]>([]);
  const [hoveredFlagId, setHoveredFlagId] = useState<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const highlightRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  // pdf.js throws if a second render() starts on the same canvas before
  // the first completes (reproduced live: clicking a flag right after
  // mount raced the initial page-1 render against the jump-to-page-5
  // render). Cancelling any in-flight task before starting a new one is
  // pdf.js's own documented fix for this, not a workaround.
  const renderTaskRef = useRef<RenderTask | null>(null);

  const flagsById = new Map(flags.map((f) => [f.id, f]));

  useEffect(() => {
    let cancelled = false;
    pdfjsLib
      .getDocument({ url: fileUrl, withCredentials: true })
      .promise.then((doc) => {
        if (!cancelled) setPdf(doc);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [fileUrl]);

  // Jump to a flag's page when it's selected/activated from the analysis
  // pane (AC3 direction 2).
  useEffect(() => {
    if (requestedPage !== null) setPage(requestedPage);
  }, [requestedPage]);

  // Found live (`ux-critic`, 2026-08-19): Prev/Next update `page` directly
  // via a functional updater, which never touched `pageInput` -- the
  // field showed a stale number after the very first Prev/Next click, and
  // its own native spin arrows then operated on that stale value instead
  // of the page actually on screen. `page` is the single source of truth;
  // the input mirrors it unconditionally (it's an editable readout with
  // its own onBlur/Enter commit path, not free-typing state that needs
  // protecting from this sync).
  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

  // Renders the canvas. Only depends on [pdf, page] -- selecting a
  // different flag on the SAME page must never re-render the page, only
  // recompute which boxes are drawn over it (below).
  useEffect(() => {
    if (!pdf || !canvasRef.current) return;
    let cancelled = false;
    pdf.getPage(page).then((pdfPage) => {
      if (cancelled) return;
      const containerWidth = wrapRef.current?.clientWidth ?? 800;
      const unscaled = pdfPage.getViewport({ scale: 1, rotation: pdfPage.rotate });
      const scale = Math.max(0.5, Math.min(2.5, containerWidth / unscaled.width));
      const nextViewport = pdfPage.getViewport({ scale, rotation: pdfPage.rotate });
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      canvas.width = nextViewport.width;
      canvas.height = nextViewport.height;
      renderTaskRef.current?.cancel();
      const task = pdfPage.render({ canvas, canvasContext: ctx, viewport: nextViewport });
      renderTaskRef.current = task;
      task.promise
        .then(() => {
          if (cancelled) return;
          setViewport(nextViewport);
        })
        .catch((err: unknown) => {
          // A cancelled render rejects with a RenderingCancelledException --
          // expected when a newer page request superseded this one, not a
          // real failure.
          if (cancelled || (err as { name?: string })?.name === "RenderingCancelledException") return;
          setLoadError(true);
        });
    });
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [pdf, page]);

  // Recomputes highlight boxes from the already-rendered viewport --
  // cheap, no canvas work, safe to run on every selection change.
  //
  // Found live (`ux-critic`, 2026-08-19): with a flag selected, showing
  // every OTHER flag's box on the same page too meant a flag with no
  // recoverable region of its own (e.g. an F7 section-kind flag) could
  // land the instructor on a page where a DIFFERENT flag's box is the
  // only one visible -- nothing distinguished it as "not yours," so it
  // read as confidently, specifically wrong. In detail mode (a flag
  // selected), only that flag's own box renders, if it has one; browsing
  // (no selection) still shows every flag on the page.
  useEffect(() => {
    if (!viewport) return;
    const pageBoxes: HighlightBox[] = [];
    let ordinal = 0;
    for (const region of regions) {
      if (region.kind !== "page_bbox" || region.page !== page || !region.bbox) continue;
      if (selectedFlagId !== null && region.flag_id !== selectedFlagId) continue;
      ordinal += 1;
      // pdfjs-dist v6's PageViewport dropped `convertToViewportRectangle`
      // (present in older examples/tutorials, reproduced live: calling it
      // throws "not a function") -- the two-corner-point form is the real
      // v6 API (`page_viewport.d.ts`: only `convertToViewportPoint` exists).
      const [x0, y0, x1, y1] = region.bbox;
      const [vx0, vy0] = viewport.convertToViewportPoint(x0, y0);
      const [vx1, vy1] = viewport.convertToViewportPoint(x1, y1);
      const left = Math.min(vx0, vx1);
      const top = Math.min(vy0, vy1);
      const width = Math.max(Math.abs(vx1 - vx0), 24);
      const height = Math.max(Math.abs(vy1 - vy0), 24);
      pageBoxes.push({
        flagId: region.flag_id,
        left,
        top,
        width,
        height,
        selected: region.flag_id === selectedFlagId,
        ordinal,
      });
    }
    setBoxes(pageBoxes);
  }, [viewport, page, regions, selectedFlagId]);

  // AC3: activating a finding from the Analysis pane focuses its
  // highlight on the document, once rendered -- a real DOM focus move,
  // not just a scroll.
  useEffect(() => {
    if (selectedFlagId === null) return;
    const el = highlightRefs.current.get(selectedFlagId);
    el?.focus({ preventScroll: false });
  }, [selectedFlagId, boxes]);

  // `ux-critic` finding (V-072 review, 2026-08-20): the highlight's own
  // popover (opened by the focus-move effect above, since a focused
  // element also counts as hovered for keyboard users) had no way to
  // dismiss -- it stayed open indefinitely and, at narrow widths, visibly
  // overlapped other content. V-072's passage flags made this far more
  // common than it was under V-065 alone (many more flags now resolve to
  // a real `page_bbox`, so the auto-focus-on-select effect above fires
  // far more often). Escape is this app's own established dismiss
  // convention (Modal.tsx already uses it) -- reused here, not invented.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setHoveredFlagId(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  function commitPageInput() {
    const n = Number(pageInput);
    if (Number.isFinite(n) && pdf) {
      const clamped = Math.max(1, Math.min(pdf.numPages, Math.round(n)));
      setPage(clamped);
      setPageInput(String(clamped));
    } else {
      setPageInput(String(page));
    }
  }

  const activePopoverFlagId = hoveredFlagId;
  const popoverFlag = activePopoverFlagId !== null ? flagsById.get(activePopoverFlagId) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-panel px-3 py-2 text-sm">
        <button
          type="button"
          onClick={() => pdf && setPage((p) => Math.max(1, p - 1))}
          disabled={!pdf || page <= 1}
          className="min-h-9 rounded-md border border-border-input px-2.5 disabled:opacity-40"
        >
          ← Prev
        </button>
        <span className="flex items-center gap-1.5">
          Page{" "}
          <input
            type="number"
            inputMode="numeric"
            aria-label="Go to page"
            min={1}
            max={pdf?.numPages ?? 1}
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={commitPageInput}
            onKeyDown={(e) => e.key === "Enter" && commitPageInput()}
            className="w-14 rounded-md border border-border-input bg-page px-1.5 py-1 text-center"
          />{" "}
          of {pdf?.numPages ?? "…"}
        </span>
        <button
          type="button"
          onClick={() => pdf && setPage((p) => Math.min(pdf.numPages, p + 1))}
          disabled={!pdf || page >= (pdf?.numPages ?? 1)}
          className="min-h-9 rounded-md border border-border-input px-2.5 disabled:opacity-40"
        >
          Next →
        </button>
      </div>
      <div ref={wrapRef} className="relative min-h-0 flex-1 overflow-auto bg-page p-4">
        {loadError && (
          <p role="alert" className="p-4 text-sm text-status-attention-text">
            This document couldn't be loaded.
          </p>
        )}
        {!pdf && !loadError && (
          <p role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-secondary">
            Loading manuscript.
          </p>
        )}
        <div className="relative mx-auto w-fit">
          <canvas ref={canvasRef} role="img" aria-label={`Page ${page} of the manuscript`} />
          {boxes.map((box) => {
            const flag = flagsById.get(box.flagId);
            return (
              <button
                key={box.flagId}
                type="button"
                ref={(el) => {
                  if (el) highlightRefs.current.set(box.flagId, el);
                  else highlightRefs.current.delete(box.flagId);
                }}
                aria-label={
                  flag
                    ? `${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind} flag, ${flag.severity} severity, page ${page}`
                    : `Flag on page ${page}`
                }
                aria-describedby={`region-popover-${box.flagId}`}
                onMouseEnter={() => setHoveredFlagId(box.flagId)}
                onMouseLeave={() => setHoveredFlagId((id) => (id === box.flagId ? null : id))}
                onFocus={() => setHoveredFlagId(box.flagId)}
                onBlur={() => setHoveredFlagId((id) => (id === box.flagId ? null : id))}
                onClick={() => onSelectFlag(box.flagId)}
                style={{
                  position: "absolute",
                  left: box.left,
                  top: box.top,
                  width: box.width,
                  height: box.height,
                  background: "color-mix(in srgb, var(--color-status-caution-text) 20%, transparent)",
                  border: `${box.selected ? 3 : 2}px solid var(--color-status-caution-text)`,
                  borderRadius: "2px",
                }}
              >
                <span className="sr-only">{box.ordinal}</span>
              </button>
            );
          })}
        </div>
        {popoverFlag && (
          <div
            id={`region-popover-${popoverFlag.id}`}
            role="tooltip"
            className="pointer-events-none fixed z-raised max-w-xs rounded-md border border-border bg-panel p-2.5 text-xs shadow-sm"
            style={{
              left: (highlightRefs.current.get(popoverFlag.id)?.getBoundingClientRect().left ?? 0) + 8,
              top: (highlightRefs.current.get(popoverFlag.id)?.getBoundingClientRect().bottom ?? 0) + 6,
            }}
          >
            <p className="font-semibold tracking-header text-ink-tertiary uppercase">
              {CHECK_KIND_SHORT_LABEL[popoverFlag.check_kind] ?? popoverFlag.check_kind}
            </p>
            <p className="mt-1 text-ink">{truncateAtWord(popoverFlag.evidence_excerpt, 140)}</p>
            <div className="mt-1.5">
              <SeverityTag severity={popoverFlag.severity as Severity} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
