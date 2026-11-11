// BUG-684: a page opened in a dialog starts at its marked excerpt, not at the
// top of the page, so the mark is in view without scrolling to find it.
import type { EvidencePage } from "./evidenceTypes";

/** How far down the visible area the mark lands: a third leaves the lines
 * above it in view for context. */
const MARK_VIEW_FRACTION = 1 / 3;

/** On the image's load, scroll the dialog that holds it so the first mark is
 * in view. Outside a dialog nothing moves: an inline preview never scrolls the
 * page under the reader. */
export function scrollToMark(image: HTMLImageElement, marks: EvidencePage["marks"]) {
  const first = marks?.[0];
  const scroller = image.closest<HTMLElement>(".signal-dialog__body");
  if (!first || !scroller) return;
  const markTop = image.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
    + first.y * image.clientHeight;
  scroller.scrollTop = Math.max(0, markTop - scroller.clientHeight * MARK_VIEW_FRACTION);
}
