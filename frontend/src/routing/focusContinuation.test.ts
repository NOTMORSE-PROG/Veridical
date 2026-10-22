import { afterEach, expect, it, vi } from "vitest";
import { claimFocusContinuation } from "./focusContinuation";

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });
function setup() {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const owner = document.createElement("button");
  const target = document.createElement("button");
  document.body.append(owner, target); owner.focus();
  return { owner, target, claim: claimFocusContinuation() };
}
it("moves explicit continuation after the final trigger is removed", () => {
  const { owner, target, claim } = setup(); owner.remove();
  expect(claim.focus(target)).toBe(true); expect(document.activeElement).toBe(target);
});
it.each(["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", "pointer", "wheel", "blur", "focus"])("retires focus permission on later %s intent", (intent) => {
  const { owner, target, claim } = setup();
  if (["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End"].includes(intent)) document.dispatchEvent(new KeyboardEvent("keydown", { key: intent }));
  if (intent === "pointer") document.dispatchEvent(new Event("pointerdown"));
  if (intent === "wheel") window.dispatchEvent(new Event("wheel"));
  if (intent === "blur") window.dispatchEvent(new Event("blur"));
  if (intent === "focus") target.focus();
  owner.remove();
  expect(claim.focus(target)).toBe(false);
});
it("disposed ownership cannot be reused", () => {
  const { target, claim } = setup(); claim.dispose(); expect(claim.focus(target)).toBe(false);
});
