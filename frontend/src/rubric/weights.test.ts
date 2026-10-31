import { describe, expect, it } from "vitest";
import { equalWeights, readableWeights } from "./weights";

describe("readable criterion weights (BUG-580)", () => {
  it("keeps weights the format states plainly", () => {
    expect(readableWeights([20, 30, 50])).toEqual([20, 30, 50]);
    expect(readableWeights([1, 1, 2])).toEqual([1, 1, 2]);
    expect(readableWeights([0.25, 0.75])).toEqual([0.25, 0.75]);
  });

  it("shows a computed split as plain weights with the same ratios", () => {
    expect(readableWeights(Array(9).fill(11.111))).toEqual(Array(9).fill(1));
    expect(readableWeights([100 / 6, 100 / 3, 50])).toEqual([1, 2, 3]);
  });

  it("says when every criterion weighs the same", () => {
    expect(equalWeights([1, 1, 1])).toBe(true);
    expect(equalWeights([1, 2])).toBe(false);
    expect(equalWeights([1])).toBe(false);
  });
});
