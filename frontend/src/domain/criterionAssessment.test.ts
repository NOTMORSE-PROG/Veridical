import { expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { criterionAssessmentDetails } from "./criterionAssessment";

const row: ResultRowCommon = { criterion_id: 1, text: "Custom requirement", type: "semantic", kind: "semantic", basis: "llm",
  weight: 1, weight_importance: "med", outcome: "passed", score: 100, reasoning: "Original automated explanation.",
  reason: null, anchor: null, evidence: [], resolution: null };

it.each([
  ["semantic", "semantic", "llm", "ai"], ["structural", "semantic", "llm", "ai"],
  ["structural", "structural", null, "rule"], ["semantic", "structural", null, "unknown"],
  ["structural", "structural", "llm", "unknown"], ["structural", "structural", "source-quality", "unknown"],
  ["structural", "structural", "not-graded", "unknown"], ["structural", "structural", "future-unknown", "unknown"],
  ["not_assessable", "semantic", "llm", "unknown"], ["unknown", "semantic", "llm", "unknown"],
  ["semantic", "semantic", null, "unknown"], ["semantic", "semantic", "source-quality", "unknown"],
  ["structural", "semantic", "structural-alignment", "unknown"],
] as const)("requires explicit source fields for %s/%s/%s", (type, kind, basis, source) => {
  expect(criterionAssessmentDetails({ ...row, type, kind, basis } as unknown as ResultRowCommon, false).source).toBe(source);
});

it("does not infer a shown instructor reason or locations from an explanation alone", () => {
  const out = criterionAssessmentDetails({ ...row, basis: null, resolution: { type: "mark_pass", reason: " ", ai_majority_verdict: null } }, false);
  expect(out.summary).toBe("Recorded assessment details");
  expect(out.relationship).toBe("The report does not identify the source of this explanation.");
  expect(out.relationship).not.toMatch(/shown above|manuscript locations/);
});

it("separates actual private reason and located excerpts without asserting their support relationship", () => {
  const out = criterionAssessmentDetails({ ...row, evidence: [{ quote: "Recorded quote", anchor: "paragraph 2" }],
    resolution: { type: "mark_pass", reason: "A separate instructor reason.", ai_majority_verdict: null } }, false);
  expect(out.summary).toBe("Earlier AI assessment"); expect(out.explanationLabel).toBe("Earlier AI explanation:");
  expect(out.relationship).toContain("The instructor's reason is shown above.");
  expect(out.relationship).toContain("The record does not link that reason to these manuscript locations.");
});

it("uses only the public resolved fact and never advertises a private reason above", () => {
  const injected: ResultRowCommon = { ...row, resolution: { type: "mark_pass", reason: "Private injected reason", ai_majority_verdict: null } };
  expect(criterionAssessmentDetails(injected, true).instructorRecorded).toBe(false);
  const shared = criterionAssessmentDetails({ ...injected, resolved: true }, true);
  expect(shared.summary).toBe("Earlier AI assessment");
  expect(shared.relationship).toContain("They do not record the instructor's reason.");
  expect(shared.relationship).not.toMatch(/shown above|Private injected/);
});

it("keeps an empty assessment empty and does not infer a location from an unlocated quote", () => {
  const empty = criterionAssessmentDetails({ ...row, reasoning: null }, false);
  expect(empty.hasDetails).toBe(false); expect(empty.relationship).toBe("");
  const unlocated = criterionAssessmentDetails({ ...row, evidence: [{ quote: "Unlocated quote", anchor: " " }],
    resolution: { type: "needs_document", reason: "An ungraded exclusion reason.", ai_majority_verdict: null } }, false);
  expect(unlocated.hasLocations).toBe(false); expect(unlocated.relationship).not.toContain("manuscript locations");
});

it("does not substitute a public resolved flag for an actual private instructor resolution", () => {
  expect(criterionAssessmentDetails({ ...row, resolved: true, resolution: null }, false).instructorRecorded).toBe(false);
});
it("does not imply an absent instructor reason is shown or bound to a genuine location", () => {
  const out = criterionAssessmentDetails({ ...row, anchor: "page 2", resolution: { type: "mark_pass", reason: " ", ai_majority_verdict: null } }, false);
  expect(out.relationship).toContain("These locations are not recorded as references supplied with an instructor reason.");
  expect(out.relationship).not.toContain("reason is shown above");
});
