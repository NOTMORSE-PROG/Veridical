import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { capturePdfPosition, restorePdfPosition, visiblePdfPage, type PdfReadingPosition } from "./pdfReadingPosition";

export function usePdfReadingPosition(
  rootRef: RefObject<HTMLDivElement | null>,
  pages: RefObject<Map<number, HTMLElement>>,
  documentKey: string,
  zoom: number,
  pageCount: number,
  publishPage: (page: number) => void,
) {
  const stable = useRef<PdfReadingPosition | null>(null);
  const pendingZoom = useRef<PdfReadingPosition | null>(null);
  const size = useRef("");
  const intent = useRef(0);
  const publish = useRef(publishPage);
  publish.current = publishPage;

  const measureSize = useCallback(() => {
    const root = rootRef.current;
    const firstPage = pages.current.values().next().value as HTMLElement | undefined;
    return root ? `${root.clientWidth}:${root.clientHeight}:${firstPage?.getBoundingClientRect().width ?? 0}` : "";
  }, [pages, rootRef]);

  const remember = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    stable.current = capturePdfPosition(root, pages.current);
    size.current = measureSize();
  }, [measureSize, pages, rootRef]);

  const retire = useCallback(() => {
    intent.current += 1;
    pendingZoom.current = null;
    remember();
  }, [remember]);

  const prepareZoom = useCallback(() => {
    retire();
    pendingZoom.current = stable.current;
  }, [retire]);

  const restore = useCallback((position: PdfReadingPosition | null) => {
    const root = rootRef.current;
    if (!root) return;
    if (position) restorePdfPosition(root, pages.current, position);
    remember();
    const page = visiblePdfPage(root, pages.current);
    if (page !== null) publish.current(page);
  }, [pages, remember, rootRef]);

  useLayoutEffect(() => {
    const position = pendingZoom.current;
    pendingZoom.current = null;
    if (position) restore(position);
  }, [zoom, restore]);

  useLayoutEffect(() => {
    intent.current += 1;
    stable.current = null;
    pendingZoom.current = null;
    size.current = "";
  }, [documentKey]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || pageCount === 0) return;
    remember();
    const observer = new ResizeObserver(() => {
      if (measureSize() !== size.current) restore(stable.current);
    });
    observer.observe(root);
    const onScroll = () => {
      // A resize can precede its observer notification. Keep the old source
      // point until that notification, rather than recording shifted geometry.
      if (measureSize() === size.current) remember();
    };
    const onKey = (event: KeyboardEvent) => {
      if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) retire();
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    root.addEventListener("wheel", retire, { passive: true });
    root.addEventListener("pointerdown", retire, { passive: true });
    root.addEventListener("keydown", onKey);
    return () => {
      observer.disconnect();
      root.removeEventListener("scroll", onScroll);
      root.removeEventListener("wheel", retire);
      root.removeEventListener("pointerdown", retire);
      root.removeEventListener("keydown", onKey);
      intent.current += 1;
      pendingZoom.current = null;
    };
  }, [documentKey, measureSize, pageCount, remember, restore, retire, rootRef]);

  return { prepareZoom, retire, intent };
}
