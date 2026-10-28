import { afterEach, describe, expect, it, vi } from "vitest";
import { revealRecordedPdfEvidence } from "./revealRecordedPdfEvidence";

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

function geometry(height = 167, span = 60) {
  const root = document.createElement("div");
  const page = document.createElement("section");
  const mark = document.createElement("span");
  mark.className = "signal-recorded-source-mark"; mark.dataset.referenceId = "current";
  root.append(page); page.append(mark); document.body.append(root);
  root.style.scrollPaddingTop = "24px"; root.scrollTop = 1000; root.scrollLeft = 37;
  Object.defineProperty(root, "clientHeight", { value: height });
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({ top: 443 } as DOMRect);
  vi.spyOn(mark, "getBoundingClientRect").mockImplementation(() => ({
    top: 615 - (root.scrollTop - 1000), bottom: 615 + span - (root.scrollTop - 1000), width: 300, height: span,
  }) as DOMRect);
  root.scrollTo = vi.fn((value?: ScrollToOptions | number, y?: number) => {
    const options = typeof value === "number" ? { top: y } : value ?? {};
    root.scrollTop = options.top ?? root.scrollTop;
  });
  return { root, page, mark };
}

describe("recorded quotation visibility", () => {
  it("reveals the full quote in the short viewport that clips a page-top placement", () => {
    const { root, page, mark } = geometry();
    expect(mark.getBoundingClientRect().top).toBeGreaterThan(443 + root.clientHeight);
    expect(revealRecordedPdfEvidence(root, page, "current")).toBe(true);
    expect(mark.getBoundingClientRect().top).toBeGreaterThanOrEqual(443 + 24);
    expect(mark.getBoundingClientRect().bottom).toBeLessThanOrEqual(443 + root.clientHeight - 24);
    expect(root.scrollLeft).toBe(37);
  });

  it("starts an oversized quotation at the visible inset without changing its marks", () => {
    const { root, page, mark } = geometry(167, 300);
    expect(revealRecordedPdfEvidence(root, page, "current")).toBe(true);
    expect(mark.getBoundingClientRect().top).toBe(443 + 24);
    expect(mark.getBoundingClientRect().height).toBe(300);
  });

  it("does not reveal another reference or a detached source page", () => {
    const { root, page } = geometry();
    expect(revealRecordedPdfEvidence(root, page, "old-reference")).toBe(false);
    page.remove();
    expect(revealRecordedPdfEvidence(root, page, "current")).toBe(false);
    expect(root.scrollTo).not.toHaveBeenCalled();
  });
});
