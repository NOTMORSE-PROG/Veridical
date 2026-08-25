// BUG-092: `resultDisplay` must distinguish a `not_assessable`-typed
// criterion's `not_applicable` outcome from an ordinary N/A -- an
// instructor seeing bare "Not applicable" with no reason can't tell a
// real defense-day requirement apart from a rubric-parsing gap.
import { describe, expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { resultDisplay, sourceCaption } from "./ResultsTable";

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

  // --- V-069: a levelled criterion's decided result ------------------------

  it("V-069 AC2: a levelled criterion shows its LEVEL NAME, not Pass/Fail", () => {
    const display = resultDisplay(
      row({
        outcome: "passed",
        score: 75,
        level: { name: "Proficient", ordinal: 3, points: 3, max_points: 4 },
      }),
    );
    expect(display.label).toBe("Proficient");
    // Not "info": that tone's icon is a real loading spinner every other
    // user of it genuinely needs (`ux-critic` finding, live-reproduced).
    expect(display.tone).toBe("level");
    expect(display.caption).toBe("3/4 pts");
  });

  it("V-069 AC3: a criterion with no level is unaffected by the level branch", () => {
    const display = resultDisplay(row({ outcome: "passed", score: 100, level: null }));
    expect(display.label).toBe("Pass");
  });
});

describe("sourceCaption", () => {
  it("an instructor-facing row with a real `resolution` reads as resolved (unchanged)", () => {
    expect(
      sourceCaption(
        row({
          resolution: { type: "mark_pass", reason: "Checked myself.", ai_majority_verdict: "fail" },
        }),
      ),
    ).toBe("Resolved by instructor");
  });

  it("V-069 (`ux-critic` finding, live-reproduced): a public/adviser row has no `resolution` (BUG-044, private) but `resolved: true` -- must still read as resolved, not fall through to AI-graded/Rule-checked", () => {
    // This is exactly PublicCriterionResultOut's real shape for a
    // resolved criterion: no `resolution` object (the reason is private),
    // but `resolved` set. Before this fix, `sourceCaption` had no signal
    // at all and showed the AI's own SUPERSEDED evidence as if final.
    expect(sourceCaption(row({ resolution: null, resolved: true, kind: "semantic" }))).toBe(
      "Resolved by instructor",
    );
  });

  it("an ordinary un-resolved row is unaffected", () => {
    expect(sourceCaption(row({ resolution: null, resolved: false, kind: "semantic" }))).toBe(
      "AI-graded",
    );
  });
});
