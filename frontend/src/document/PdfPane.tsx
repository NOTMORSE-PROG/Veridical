import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import type { FlagRegionOut, FlagSummaryOut } from "../api/types";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { severityLabel } from "../domain/severity";
import { truncateAtWord } from "../format/text";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface PageMetric {
  pageNumber: number;
  width: number;
  height: number;
}

interface HighlightRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface HighlightRegion {
  flagId: number;
  findingNumber: number;
  selected: boolean;
  rects: HighlightRect[];
}

const EMPTY_FINDING_NUMBERS: ReadonlyMap<number, number> = new Map();
const PDF_ZOOM_MIN = 0.8;
const PDF_ZOOM_MAX = 1.8;
const PDF_ZOOM_STEP = 0.2;

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function ContinuousPdfPage({
  pdf,
  metric,
  totalPages,
  scrollRoot,
  regions,
  flagsById,
  selectedFlagId,
  findingNumbers,
  onSelectFlag,
  onVisibility,
  registerPage,
  focusSelection,
  readingPage,
}: {
  pdf: PDFDocumentProxy;
  metric: PageMetric;
  totalPages: number;
  scrollRoot: RefObject<HTMLDivElement | null>;
  regions: FlagRegionOut[];
  flagsById: ReadonlyMap<number, FlagSummaryOut>;
  selectedFlagId: number | null;
  findingNumbers: ReadonlyMap<number, number>;
  onSelectFlag: (flagId: number) => void;
  onVisibility: (page: number, ratio: number) => void;
  registerPage: (page: number, element: HTMLElement | null) => void;
  focusSelection: boolean;
  readingPage: number;
}) {
  const pageRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const markerRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const renderTaskRef = useRef<RenderTask | null>(null);
  const textLayerTaskRef = useRef<pdfjsLib.TextLayer | null>(null);
  const [isNear, setIsNear] = useState(metric.pageNumber <= 2);
  const shouldRender = isNear || metric.pageNumber === readingPage;
  const [displayWidth, setDisplayWidth] = useState(0);
  const [viewport, setViewport] = useState<ReturnType<PDFPageProxy["getViewport"]> | null>(null);
  const [hoveredFlagId, setHoveredFlagId] = useState<number | null>(null);

  useEffect(() => {
    const element = pageRef.current;
    const root = scrollRoot.current;
    if (!element || !root) return;
    registerPage(metric.pageNumber, element);
    const nearObserver = new IntersectionObserver(
      ([entry]) => setIsNear(entry.isIntersecting),
      { root, rootMargin: "100% 0px" },
    );
    const visibilityObserver = new IntersectionObserver(
      ([entry]) => onVisibility(metric.pageNumber, entry.intersectionRatio),
      { root, threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    nearObserver.observe(element);
    visibilityObserver.observe(element);
    return () => {
      registerPage(metric.pageNumber, null);
      nearObserver.disconnect();
      visibilityObserver.disconnect();
    };
  }, [metric.pageNumber, onVisibility, registerPage, scrollRoot]);

  useEffect(() => {
    const element = pageRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setDisplayWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!shouldRender || displayWidth <= 0 || !canvasRef.current) return;
    let cancelled = false;
    let pageProxy: PDFPageProxy | null = null;
    const canvas = canvasRef.current;
    const textContainer = textLayerRef.current;

    pdf.getPage(metric.pageNumber).then(async (page) => {
      if (cancelled) return;
      pageProxy = page;
      const unscaled = page.getViewport({ scale: 1, rotation: page.rotate });
      const displayScale = displayWidth / unscaled.width;
      const displayViewport = page.getViewport({ scale: displayScale, rotation: page.rotate });
      const outputScale = window.devicePixelRatio || 1;
      const renderViewport = page.getViewport({ scale: displayScale * outputScale, rotation: page.rotate });
      const context = canvas.getContext("2d");
      if (!context) return;

      canvas.width = Math.ceil(renderViewport.width);
      canvas.height = Math.ceil(renderViewport.height);
      canvas.style.width = `${displayViewport.width}px`;
      canvas.style.height = `${displayViewport.height}px`;
      renderTaskRef.current?.cancel();
      const renderTask = page.render({ canvas, canvasContext: context, viewport: renderViewport });
      renderTaskRef.current = renderTask;
      await renderTask.promise;
      if (cancelled) return;
      setViewport(displayViewport);

      if (textContainer) {
        textLayerTaskRef.current?.cancel();
        textContainer.replaceChildren();
        const textContent = await page.getTextContent();
        if (cancelled) return;
        textContainer.style.setProperty("--total-scale-factor", String(displayScale));
        const textLayer = new pdfjsLib.TextLayer({
          textContentSource: textContent,
          container: textContainer,
          viewport: displayViewport,
        });
        textLayerTaskRef.current = textLayer;
        await textLayer.render();
      }
    }).catch((error: unknown) => {
      if (cancelled || (error as { name?: string })?.name === "RenderingCancelledException") return;
      setViewport(null);
    });

    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      textLayerTaskRef.current?.cancel();
      pageProxy?.cleanup();
    };
  }, [displayWidth, shouldRender, metric.pageNumber, pdf]);

  useEffect(() => {
    if (!focusSelection || selectedFlagId === null || selectedFlagId < 0 || !isNear) return;
    markerRefs.current.get(selectedFlagId)?.focus({ preventScroll: false });
  }, [focusSelection, isNear, selectedFlagId, viewport]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setHoveredFlagId(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const highlights = useMemo<HighlightRegion[]>(() => {
    if (!viewport) return [];
    return regions.flatMap((region) => {
      if (region.kind !== "page_bbox" || region.page !== metric.pageNumber || !region.bbox) return [];
      if (selectedFlagId !== null && region.flag_id !== selectedFlagId) return [];
      const sourceRects = region.all_bboxes.length > 0 ? region.all_bboxes : [region.bbox];
      const rects = sourceRects.map(([x0, y0, x1, y1]) => {
        const [vx0, vy0] = viewport.convertToViewportPoint(x0, y0);
        const [vx1, vy1] = viewport.convertToViewportPoint(x1, y1);
        return {
          left: Math.min(vx0, vx1),
          top: Math.min(vy0, vy1),
          width: Math.max(Math.abs(vx1 - vx0), 24),
          height: Math.max(Math.abs(vy1 - vy0), 24),
        };
      });
      return [{
        flagId: region.flag_id,
        findingNumber: findingNumbers.get(region.flag_id) ?? 1,
        selected: region.flag_id === selectedFlagId,
        rects,
      }];
    });
  }, [findingNumbers, metric.pageNumber, regions, selectedFlagId, viewport]);

  const popoverFlag = hoveredFlagId !== null && hoveredFlagId !== selectedFlagId
    ? flagsById.get(hoveredFlagId)
    : null;
  const aspectStyle = { aspectRatio: `${metric.width} / ${metric.height}` } as CSSProperties;

  return (
    <section
      ref={(element) => {
        pageRef.current = element;
      }}
      className="signal-document-pdf-page"
      style={aspectStyle}
      aria-label={`Page ${metric.pageNumber} of ${totalPages}`}
      data-page={metric.pageNumber}
    >
      {shouldRender ? (
        <>
          <canvas ref={canvasRef} role="img" aria-label={`Rendered page ${metric.pageNumber} of the manuscript`} />
          <div ref={textLayerRef} className="signal-document-pdf-text-layer" aria-hidden="true" />
          <div className="signal-document-pdf-evidence-layer">
            {highlights.map((highlight) => {
              const flag = flagsById.get(highlight.flagId);
              return highlight.rects.map((rect, index) => {
                const position = {
                  left: rect.left,
                  top: rect.top,
                  width: rect.width,
                  height: rect.height,
                } as CSSProperties;
                if (index > 0) {
                  return (
                    <span
                      key={`${highlight.flagId}-${index}`}
                      aria-hidden="true"
                      className={`signal-document-evidence-region signal-document-evidence-region--continuation${highlight.selected ? " is-selected" : ""}`}
                      style={position}
                    />
                  );
                }
                return (
                  <button
                    key={highlight.flagId}
                    type="button"
                    ref={(element) => {
                      if (element) markerRefs.current.set(highlight.flagId, element);
                      else markerRefs.current.delete(highlight.flagId);
                    }}
                    className={`signal-document-evidence-region${highlight.selected ? " is-selected" : ""}`}
                    style={position}
                    aria-label={flag
                      ? `Finding ${highlight.findingNumber}: ${CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}. ${severityLabel(flag.severity)}. Page ${metric.pageNumber}. Exact passage.${highlight.selected ? " Selected." : ""}`
                      : `${highlight.flagId < 0 ? "Criterion evidence" : `Finding ${highlight.findingNumber}`} on page ${metric.pageNumber}. Exact passage.${highlight.selected ? " Selected." : ""}`}
                    aria-pressed={highlight.selected}
                    aria-describedby={popoverFlag?.id === highlight.flagId ? `region-popover-${highlight.flagId}` : undefined}
                    onMouseEnter={() => setHoveredFlagId(highlight.flagId)}
                    onMouseLeave={() => setHoveredFlagId((id) => id === highlight.flagId ? null : id)}
                    onFocus={() => setHoveredFlagId(highlight.flagId)}
                    onBlur={() => setHoveredFlagId((id) => id === highlight.flagId ? null : id)}
                    onClick={() => onSelectFlag(highlight.flagId)}
                  >
                    <span className="signal-document-marker" aria-hidden="true">{highlight.flagId < 0 ? "Evidence" : highlight.findingNumber}</span>
                  </button>
                );
              });
            })}
          </div>
          {popoverFlag && (
            <div id={`region-popover-${popoverFlag.id}`} role="tooltip" className="signal-document-marker-tooltip">
              <p>{CHECK_KIND_SHORT_LABEL[popoverFlag.check_kind] ?? popoverFlag.check_kind}</p>
              <p>{truncateAtWord(popoverFlag.evidence_excerpt, 140)}</p>
              <SeverityTag severity={popoverFlag.severity as Severity} />
            </div>
          )}
        </>
      ) : <span className="signal-document-page-placeholder" aria-hidden="true" />}
      <span className="signal-document-page-number" aria-hidden="true">{metric.pageNumber}</span>
    </section>
  );
}

export function PdfPane({
  fileUrl,
  regions,
  flags,
  selectedFlagId,
  onSelectFlag,
  requestedPage,
  findingNumbers = EMPTY_FINDING_NUMBERS,
  requestedPageAttempt,
  focusSelection = true,
  isVisible = true,
}: {
  fileUrl: string;
  regions: FlagRegionOut[];
  flags: FlagSummaryOut[];
  selectedFlagId: number | null;
  onSelectFlag: (flagId: number) => void;
  requestedPage: number | null;
  findingNumbers?: ReadonlyMap<number, number>;
  requestedPageAttempt?: number;
  focusSelection?: boolean;
  isVisible?: boolean;
}) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [metrics, setMetrics] = useState<PageMetric[]>([]);
  const [loadedFileUrl, setLoadedFileUrl] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [zoom, setZoom] = useState(1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLElement>>(new Map());
  const appliedPageRequest = useRef<string | null>(null);
  const sourceVisible = useRef(isVisible);
  sourceVisible.current = isVisible;
  const wasVisible = useRef(isVisible);
  const lastVisiblePage = useRef(1);
  const pageContentWidth = useRef(0);
  const resizing = useRef(false);
  const navigationGeneration = useRef(0);
  const settledPageRequest = useRef<string | null>(null);
  const activePageRequest = useRef<string | null>(null);
  activePageRequest.current = requestedPage === null ? null : `${fileUrl}:${requestedPage}:${requestedPageAttempt ?? "initial"}`;
  const flagsById = useMemo(() => new Map(flags.map((flag) => [flag.id, flag])), [flags]);

  useEffect(() => {
    let cancelled = false;
    setLoadedFileUrl(null);
    appliedPageRequest.current = null;
    settledPageRequest.current = null;
    pageContentWidth.current = 0;
    lastVisiblePage.current = 1;
    navigationGeneration.current += 1;
    setPdf(null);
    setMetrics([]);
    setLoadError(false);
    const task = pdfjsLib.getDocument({ url: fileUrl, withCredentials: true });
    task.promise.then(async (document) => {
      if (cancelled) return;
      setPdf(document);
      const pageMetrics = await Promise.all(
        Array.from({ length: document.numPages }, async (_, index) => {
          const page = await document.getPage(index + 1);
          const viewport = page.getViewport({ scale: 1, rotation: page.rotate });
          return { pageNumber: index + 1, width: viewport.width, height: viewport.height };
        }),
      );
      if (!cancelled) { setMetrics(pageMetrics); setLoadedFileUrl(fileUrl); }
    }).catch(() => {
      if (!cancelled) setLoadError(true);
    });
    return () => {
      cancelled = true;
      task.destroy();
    };
  }, [fileUrl]);

  useEffect(() => setPageInput(String(currentPage)), [currentPage]);

  const registerPage = useCallback((page: number, element: HTMLElement | null) => {
    if (element) pageRefs.current.set(page, element);
    else pageRefs.current.delete(page);
  }, []);

  const updateVisibility = useCallback(() => {
    if (!sourceVisible.current || resizing.current) return;
    if (activePageRequest.current !== null && settledPageRequest.current !== activePageRequest.current) return;
    const width = scrollRef.current?.querySelector(".signal-document-pdf-pages")?.getBoundingClientRect().width ?? 0;
    // A visibility/scroll event may run before ResizeObserver. Do not replace
    // the reading page with a page inferred from newly resized geometry.
    if (width > 0 && pageContentWidth.current > 0 && width !== pageContentWidth.current) return;
    const root = scrollRef.current?.getBoundingClientRect();
    if (!root) return;
    let mostVisiblePage = 0;
    let mostVisibleHeight = 0;
    for (const [page, element] of pageRefs.current) {
      const bounds = element.getBoundingClientRect();
      const visibleHeight = Math.max(0, Math.min(bounds.bottom, root.bottom) - Math.max(bounds.top, root.top));
      if (visibleHeight > mostVisibleHeight) {
        mostVisiblePage = page;
        mostVisibleHeight = visibleHeight;
      }
    }
    // IntersectionObserver thresholds alone retain stale ratios while a
    // smooth page jump ends between thresholds. Measure the current scroll.
    if (mostVisiblePage) { lastVisiblePage.current = mostVisiblePage; setCurrentPage(mostVisiblePage); }
  }, []);

  const scrollToPage = useCallback((page: number, behavior: ScrollBehavior = "smooth") => {
    navigationGeneration.current += 1;
    const total = pdf?.numPages ?? metrics.length;
    if (total <= 0) return;
    const target = Math.max(1, Math.min(total, Math.round(page)));
    const root = scrollRef.current;
    const element = pageRefs.current.get(target);
    if (root && element) {
      // Scroll the manuscript pane without moving the page toolbar beneath
      // the application's fixed navigation.
      const padding = Number.parseFloat(getComputedStyle(root).scrollPaddingTop) || 0;
      root.scrollTo({
        top: root.scrollTop + element.getBoundingClientRect().top
          - root.getBoundingClientRect().top - root.clientTop - padding,
        behavior,
      });
    }
    setCurrentPage(target);
    lastVisiblePage.current = target;
    setPageInput(String(target));
  }, [metrics.length, pdf?.numPages]);

  useLayoutEffect(() => {
    const request = `${fileUrl}:${requestedPage}:${requestedPageAttempt ?? "initial"}`;
    const showingAgain = isVisible && !wasVisible.current;
    wasVisible.current = isVisible;
    if (!isVisible || metrics.length === 0 || loadedFileUrl !== fileUrl) return;
    const pending = requestedPage !== null && appliedPageRequest.current !== request;
    if (!pending && !showingAgain) return;
    const target = pending ? requestedPage! : lastVisiblePage.current;
    let settleFrame = 0;
    const frame = requestAnimationFrame(() => {
      scrollToPage(target, "auto");
      if (pending) appliedPageRequest.current = request;
      const generation = navigationGeneration.current;
      settleFrame = requestAnimationFrame(() => {
        if (sourceVisible.current && navigationGeneration.current === generation) scrollToPage(target, "auto");
        settledPageRequest.current = request;
      });
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(settleFrame); };
  }, [fileUrl, loadedFileUrl, isVisible, metrics.length, requestedPage, requestedPageAttempt, scrollToPage]);

  useEffect(() => {
    const pages = scrollRef.current?.querySelector(".signal-document-pdf-pages");
    if (!pages || !isVisible || metrics.length === 0 || loadedFileUrl !== fileUrl || typeof ResizeObserver === "undefined") return;
    let initialized = false;
    let frame = 0;
    let releaseFrame = 0;
    let target: number | null = null;
    let generation = navigationGeneration.current;
    const request = `${fileUrl}:${requestedPage}:${requestedPageAttempt ?? "initial"}`;
    const observer = new ResizeObserver(() => {
      const width = pages.getBoundingClientRect().width;
      if (width <= 0) return;
      const changed = initialized && pageContentWidth.current !== width;
      pageContentWidth.current = width;
      initialized = true;
      if (!changed || !sourceVisible.current || (requestedPage !== null && settledPageRequest.current !== request)) return;
      if (target === null) { target = lastVisiblePage.current; generation = navigationGeneration.current; }
      resizing.current = true;
      cancelAnimationFrame(frame);
      cancelAnimationFrame(releaseFrame);
      frame = requestAnimationFrame(() => {
        if (sourceVisible.current && navigationGeneration.current === generation && target !== null) scrollToPage(target, "auto");
        releaseFrame = requestAnimationFrame(() => { resizing.current = false; target = null; updateVisibility(); });
      });
    });
    observer.observe(pages);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); cancelAnimationFrame(releaseFrame); resizing.current = false; };
  }, [fileUrl, loadedFileUrl, isVisible, metrics.length, requestedPage, requestedPageAttempt, scrollToPage, updateVisibility]);

  function commitPageInput() {
    const page = Number(pageInput);
    if (Number.isFinite(page)) scrollToPage(page, "auto");
    else setPageInput(String(currentPage));
  }

  const pagesLabel = pdf ? `${pdf.numPages} ${pdf.numPages === 1 ? "page" : "pages"}` : "Loading pages";
  const zoomStyle = { inlineSize: `${zoom * 100}%` } as CSSProperties;

  return (
    <div className="signal-document-source">
      <div className="signal-document-toolbar">
        <div className="signal-document-toolbar__identity">
          <strong>Full manuscript</strong>
          <span>Original PDF · {pagesLabel}</span>
        </div>
        <div className="signal-document-toolbar__controls" role="group" aria-label="PDF page and zoom controls">
          <button type="button" onClick={() => setZoom((value) => Math.max(PDF_ZOOM_MIN, Number((value - PDF_ZOOM_STEP).toFixed(1))))} disabled={zoom <= PDF_ZOOM_MIN}>Zoom out</button>
          <button type="button" onClick={() => setZoom(1)} disabled={zoom === 1}>Fit width</button>
          <button type="button" onClick={() => setZoom((value) => Math.min(PDF_ZOOM_MAX, Number((value + PDF_ZOOM_STEP).toFixed(1))))} disabled={zoom >= PDF_ZOOM_MAX}>Zoom in</button>
          <label>
            <span>Go to page</span>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={pdf?.numPages ?? 1}
              value={pageInput}
              onChange={(event) => setPageInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                commitPageInput();
              }}
            />
          </label>
          <button type="button" aria-label="Go to entered page" onClick={commitPageInput} disabled={!pdf || !pageInput.trim()}>Go</button>
          <output aria-live="polite">Page {currentPage} of {pdf?.numPages ?? "…"}</output>
        </div>
      </div>
      <div ref={scrollRef} onScroll={updateVisibility} role="region" tabIndex={0} aria-label="Full manuscript, scrollable" className="signal-document-scroll signal-document-scroll--pdf">
        {loadError ? (
          <section className="signal-document-paper-state" role="alert">
            <p className="signal-section-kicker">PDF display unavailable</p>
            <h2>The PDF could not be displayed</h2>
            <p>This source view failed to load. Any review information that already loaded is still available.</p>
          </section>
        ) : !pdf || metrics.length === 0 ? (
          <section className="signal-document-pdf-skeleton" role="status" aria-live="polite" aria-busy="true">
            <span><SpinnerIcon /> Loading full manuscript.</span>
            <i /><i /><i />
          </section>
        ) : (
          <div className="signal-document-pdf-pages" style={zoomStyle}>
            {metrics.map((metric) => (
              <ContinuousPdfPage
                key={metric.pageNumber}
                pdf={pdf}
                metric={metric}
                totalPages={pdf.numPages}
                scrollRoot={scrollRef}
                regions={regions}
                flagsById={flagsById}
                selectedFlagId={selectedFlagId}
                findingNumbers={findingNumbers}
                onSelectFlag={onSelectFlag}
                onVisibility={updateVisibility}
                registerPage={registerPage}
                focusSelection={focusSelection}
                readingPage={currentPage}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
