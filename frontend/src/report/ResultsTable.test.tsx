// BUG-092: `resultDisplay` must distinguish a `not_assessable`-typed
// criterion's `not_applicable` outcome from an ordinary N/A -- an
// instructor seeing bare "Not applicable" with no reason can't tell a
// real defense-day requirement apart from a rubric-parsing gap.
import { describe, expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { resultDisplay } from "./ResultsTable";

function row(overrides: Partial<ResultRowCommon> = {}): ResultRowCommon {
  return {
    criterion_id: 1,
    text: "The group brings three bound copies of the paper to the defense.",
    type: "structural",
    weight: 10,
    weight_importance: "med",
    kind: "structural",
    outcome: "not_applicable",
    score: null,
    basis: null,
    anchor: null,
    reasoning: null,
    reason: null,
    evidence: [],
    resolution: null,
    ...overrides,
  };
}

describe("resultDisplay", () => {
  it("BUG-092: a not_assessable criterion reads distinctly from an ordinary N/A", () => {
    const display = resultDisplay(row({ type: "not_assessable" }));
    expect(display.label).toBe("Not from the document");
    expect(display.caption).toMatch(/observed at the defense/);
  });

  it("an ordinary not_applicable criterion is unaffected", () => {
    const display = resultDisplay(row({ type: "structural" }));
    expect(display.label).toBe("Not applicable");
    expect(display.caption).toBeUndefined();
  });

  it("BUG-092: a not_assessable TYPE with a non-not_applicable outcome never leaks the special label", () => {
    // Defensive: today the router always pairs not_assessable with
    // not_applicable, but resultDisplay switches on outcome first --
    // confirms that ordering can't accidentally show the wrong label if
    // that pairing ever changes.
    const display = resultDisplay(row({ type: "not_assessable", outcome: "passed", score: 100 }));
    expect(display.label).toBe("Pass");
  });
});
