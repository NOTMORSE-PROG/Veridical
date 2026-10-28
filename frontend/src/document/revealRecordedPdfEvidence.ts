/** Reveal already-rendered, source-bound marks without changing their geometry. */
export function revealRecordedPdfEvidence(root: HTMLElement, page: HTMLElement, referenceId: string): boolean {
  if (!root.contains(page) || root.clientHeight <= 0) return false;
  const marks = Array.from(page.querySelectorAll<HTMLElement>(".signal-recorded-source-mark[data-reference-id]"))
    .filter(mark => mark.dataset.referenceId === referenceId)
    .map(mark => mark.getBoundingClientRect())
    .filter(rect => rect.width > 0 && rect.height > 0 && [rect.top, rect.bottom].every(Number.isFinite));
  if (marks.length === 0) return false;
  const top = Math.min(...marks.map(rect => rect.top));
  const bottom = Math.max(...marks.map(rect => rect.bottom));
  const visibleTop = root.getBoundingClientRect().top + root.clientTop;
  const padding = Number.parseFloat(getComputedStyle(root).scrollPaddingTop) || 0;
  const offset = bottom - top <= root.clientHeight - padding * 2
    ? (top + bottom) / 2 - (visibleTop + root.clientHeight / 2)
    : top - visibleTop - padding;
  root.scrollTo({ top: Math.max(0, root.scrollTop + offset), behavior: "auto" });
  return true;
}
