/** Position evidence below the reader's measured sticky controls. */
export function scrollReviewEvidence(target: HTMLElement, inspector: HTMLElement) {
  const clearance = parseFloat(getComputedStyle(inspector).scrollPaddingBlockStart) || 0;
  inspector.scrollTo({ top: inspector.scrollTop + target.getBoundingClientRect().top
    - inspector.getBoundingClientRect().top - clearance, behavior: "auto" });
}
