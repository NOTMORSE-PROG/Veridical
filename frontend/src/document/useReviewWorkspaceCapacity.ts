import { useCallback, useLayoutEffect, useRef, useState } from "react";

/** Measure the same token-sized tracks that the source/review layout uses. */
export function useReviewWorkspaceCapacity(onCollapse?: (active: Element | null) => void) {
  const [frameElement, setFrameElement] = useState<HTMLElement | null>(null);
  const frame = useCallback((element: HTMLElement | null) => { setFrameElement(element); }, []);
  const threshold = useRef<HTMLSpanElement>(null);
  const [split, setSplit] = useState(true);
  const measured = useRef<boolean | null>(null);
  const collapse = useRef(onCollapse);
  collapse.current = onCollapse;
  useLayoutEffect(() => {
    const element = frameElement;
    if (!element) return;
    const measure = () => {
      const minimum = threshold.current?.getBoundingClientRect().width ?? 0;
      const available = element.clientWidth;
      if (minimum <= 0 || available <= 0) return;
      const next = available >= minimum;
      if (measured.current !== false && !next) collapse.current?.(document.activeElement);
      measured.current = next;
      setSplit(next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (threshold.current) observer.observe(threshold.current);
    return () => observer.disconnect();
  }, [frameElement]);
  return { frame, threshold, split };
}
