import { expect, it } from "vitest";
import { scrollToMark } from "./scrollToMark";

function setup(inDialog: boolean) {
  const scroller = document.createElement("div");
  if (inDialog) scroller.className = "signal-dialog__body";
  const image = document.createElement("img");
  scroller.appendChild(image);
  document.body.appendChild(scroller);
  Object.defineProperty(scroller, "clientHeight", { value: 600 });
  Object.defineProperty(image, "clientHeight", { value: 1500 });
  scroller.getBoundingClientRect = () => ({ top: 100 }) as DOMRect;
  image.getBoundingClientRect = () => ({ top: 140 }) as DOMRect;
  return { scroller, image };
}

it("opens a dialog's page with the first mark a third of the way down", () => {
  const { scroller, image } = setup(true);
  scrollToMark(image, [{ x: 0.1, y: 0.6, width: 0.5, height: 0.02 }]);
  // mark top = 40 (image offset) + 0.6 * 1500 = 940; minus a third of 600.
  expect(scroller.scrollTop).toBe(740);
});

it("leaves the page alone outside a dialog, or without a mark", () => {
  const inline = setup(false);
  scrollToMark(inline.image, [{ x: 0, y: 0.6, width: 0.1, height: 0.1 }]);
  expect(inline.scroller.scrollTop).toBe(0);
  const unmarked = setup(true);
  scrollToMark(unmarked.image, []);
  expect(unmarked.scroller.scrollTop).toBe(0);
});
