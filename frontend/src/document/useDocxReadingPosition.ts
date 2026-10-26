import { useLayoutEffect, useRef, type RefObject } from "react";
import { capturePdfPosition, restorePdfPosition, type PdfReadingPosition } from "./pdfReadingPosition";

const SOURCE_READING_PLACED = "source-reading-placed";

export function recordSourceReadingPlacement(root: HTMLElement) {
  root.dispatchEvent(new Event(SOURCE_READING_PLACED));
}

/** Reading state uses real body items; it never supplies evidence coordinates. */
export function useDocxReadingPosition(rootRef: RefObject<HTMLDivElement | null>, visible: boolean, enabled = true) {
  const reading = useRef<PdfReadingPosition | null>(null);
  const size = useRef("");
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root || !enabled) return;
    const dimensions = () => root.clientWidth > 0 && root.clientHeight > 0
      ? `${root.clientWidth}:${root.clientHeight}` : null;
    const items = () => {
      const result = new Map<number, HTMLElement>();
      for (const element of root.querySelectorAll<HTMLElement>("[data-source-paragraph]")) {
        const index = Number(element.dataset.sourceParagraph);
        if (Number.isSafeInteger(index) && index >= 0 && !result.has(index)) result.set(index, element);
      }
      return result;
    };
    const remember = () => {
      const next = dimensions();
      if (!next) return;
      reading.current = capturePdfPosition(root, items());
      size.current = next;
    };
    const restore = () => {
      if (!visible) return;
      const next = dimensions();
      if (!next) return;
      if (reading.current && next !== size.current) restorePdfPosition(root, items(), reading.current);
      remember();
    };
    const onScroll = () => {
      if (dimensions() === size.current) remember();
    };
    if (visible) restore();
    const observer = new ResizeObserver(restore);
    observer.observe(root);
    root.addEventListener("scroll", onScroll, { passive: true });
    root.addEventListener(SOURCE_READING_PLACED, remember);
    return () => {
      observer.disconnect();
      root.removeEventListener("scroll", onScroll);
      root.removeEventListener(SOURCE_READING_PLACED, remember);
    };
  }, [rootRef, visible, enabled]);
}
