import { describe, expect, it } from "vitest";
import { evidenceOverlayRects } from "./evidenceOverlay";
import type { EvidencePlacement } from "../report/evidenceTypes";

const placement: EvidencePlacement = { reference_id: "a".repeat(64), state: "text_layer_match", page: 2,
  paragraph: null, boxes: [[1, 2, 4, 7]], reason: null };

describe("recorded source mark geometry", () => {
  it("keeps a small real passage rectangle without inflating its hit area", () => {
    expect(evidenceOverlayRects(placement, 2, (x, y) => [x * 2, y * 2]))
      .toEqual([{ left: 2, top: 4, width: 6, height: 10 }]);
  });
  it("uses rotated viewport coordinates", () => {
    expect(evidenceOverlayRects(placement, 2, (x, y) => [100 - y, x]))
      .toEqual([{ left: 93, top: 1, width: 5, height: 3 }]);
  });
  it("withholds marks for other pages, coarse states and invalid geometry", () => {
    expect(evidenceOverlayRects(placement, 1, (x, y) => [x, y])).toEqual([]);
    expect(evidenceOverlayRects({ ...placement, state: "page_only" }, 2, (x, y) => [x, y])).toEqual([]);
    expect(evidenceOverlayRects({ ...placement, boxes: [[1, 2, Infinity, 7], [4, 2, 1, 7]] }, 2, (x, y) => [x, y])).toEqual([]);
  });
});
