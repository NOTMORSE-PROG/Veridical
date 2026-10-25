export interface PdfReadingPosition {
  page: number;
  x: number;
  y: number;
  insetX: number;
  insetY: number;
}

function readingViewport(root: HTMLElement) {
  const rect = root.getBoundingClientRect();
  const style = getComputedStyle(root);
  return {
    left: rect.left + root.clientLeft + (Number.parseFloat(style.scrollPaddingLeft) || 0),
    top: rect.top + root.clientTop + (Number.parseFloat(style.scrollPaddingTop) || 0),
    right: rect.left + root.clientLeft + root.clientWidth,
    bottom: rect.top + root.clientTop + root.clientHeight,
  };
}

export function capturePdfPosition(root: HTMLElement, pages: ReadonlyMap<number, HTMLElement>): PdfReadingPosition | null {
  if (root.clientWidth <= 0 || root.clientHeight <= 0) return null;
  const viewport = readingViewport(root);
  for (const [page, element] of pages) {
    const rect = element.getBoundingClientRect();
    if (rect.height <= 0 || rect.width <= 0 || rect.bottom <= viewport.top || rect.top >= viewport.bottom) continue;
    const left = Math.max(rect.left, viewport.left);
    const top = Math.max(rect.top, viewport.top);
    return {
      page,
      x: Math.max(0, Math.min(1, (left - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (top - rect.top) / rect.height)),
      insetX: left - viewport.left,
      insetY: top - viewport.top,
    };
  }
  return null;
}

export function restorePdfPosition(root: HTMLElement, pages: ReadonlyMap<number, HTMLElement>, position: PdfReadingPosition): boolean {
  const element = pages.get(position.page);
  if (!element || root.clientWidth <= 0 || root.clientHeight <= 0) return false;
  const rect = element.getBoundingClientRect();
  if (rect.height <= 0 || rect.width <= 0) return false;
  const viewport = readingViewport(root);
  root.scrollTo({
    top: root.scrollTop + rect.top + rect.height * position.y - viewport.top - position.insetY,
    left: root.scrollLeft + rect.left + rect.width * position.x - viewport.left - position.insetX,
    behavior: "instant",
  });
  return true;
}

export function visiblePdfPage(root: HTMLElement, pages: ReadonlyMap<number, HTMLElement>): number | null {
  if (root.clientWidth <= 0 || root.clientHeight <= 0) return null;
  const viewport = readingViewport(root);
  let page: number | null = null;
  let largest = 0;
  for (const [candidate, element] of pages) {
    const rect = element.getBoundingClientRect();
    if (rect.height <= 0 || rect.width <= 0) continue;
    const ratio = Math.max(0, Math.min(rect.bottom, viewport.bottom) - Math.max(rect.top, viewport.top))
      * Math.max(0, Math.min(rect.right, viewport.right) - Math.max(rect.left, viewport.left))
      / (rect.height * rect.width);
    if (ratio > largest) { largest = ratio; page = candidate; }
  }
  return page;
}
