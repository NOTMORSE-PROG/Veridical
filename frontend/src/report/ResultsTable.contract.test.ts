// BUG-082: `sourceCaption` (this file's own `ResultsTable.tsx`) and
// `export.py`'s `_source_caption` implement the identical "Rule-checked
// vs AI-graded" rule in two languages -- they diverged silently (one read
// `type`, the other `kind`) with no test to catch it. This asserts THIS
// implementation against the shared contract fixture; the backend has
// its own sibling test asserting the same fixture against `_source_
// caption` (`backend/tests/test_report_export_live.py`). See `tests/
// fixtures/README.md`.
import { describe, expect, it } from "vitest";
import type { ResolutionOut, ResultRowCommon } from "../api/types";
import { sourceCaption } from "./ResultsTable";
import cases from "../../../tests/fixtures/source_caption_cases.json";

const RESOLUTION: ResolutionOut = {
  type: "mark_pass",
  reason: "Verified manually.",
  ai_majority_verdict: null,
};

function rowFor(c: (typeof cases)[number]): ResultRowCommon {
  return {
    criterion_id: 1,
    text: "Has a references section",
    type: c.type as ResultRowCommon["type"],
    weight: 10,
    weight_importance: "med",
    kind: c.kind,
    outcome: "passed",
    score: 100,
    basis: null,
    anchor: null,
    reasoning: null,
    reason: null,
    evidence: [],
    resolution: c.has_resolution ? RESOLUTION : null,
  };
}

describe("sourceCaption matches the shared contract fixture", () => {
  for (const c of cases) {
    it(c.name, () => {
      expect(sourceCaption(rowFor(c))).toBe(c.expected_caption);
    });
  }
});
