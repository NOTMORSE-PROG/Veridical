import { describe, expect, it } from "vitest";
import type { CriterionResultOut, EscalationResolution, ResolveEscalationOut } from "../api/types";
import { confirmedJudgment } from "./savedJudgment";

const reason = "A reason grounded in this criterion's source.";
const level = { ordinal: 1, name: "Custom first level", points: 1, max_points: 3 };
function record(type: EscalationResolution, fields: Partial<CriterionResultOut> = {}): CriterionResultOut {
  return { criterion_id: 7, text: "Custom criterion", type: "semantic", kind: "semantic", weight: 1,
    weight_importance: "med", outcome: "passed", score: 100, basis: "llm", anchor: null,
    reasoning: null, reason: null, evidence: [], resolution: { type, reason, ai_majority_verdict: null }, ...fields };
}
function response(row: CriterionResultOut) {
  return { check_result_id: 11, outcome: row.outcome, score: row.score,
    report: { check_run_id: 5, results: [row] } } as ResolveEscalationOut;
}
const valid = [
  ["binary pass", "mark_pass", record("mark_pass")],
  ["binary fail", "mark_fail", record("mark_fail", { outcome: "failed", score: 0 })],
  ["ungraded exclusion", "needs_document", record("needs_document", { outcome: "not_applicable", score: null })],
  ["database-rounded third", "mark_level", record("mark_level", { score: 33.33, level })],
  ["zero-point named level", "mark_level", record("mark_level", { score: 0, level: { ...level, points: 0 } })],
  ["partial majority", "accept_majority", record("accept_majority", { score: 50 })],
] as const;

describe("confirmed saved judgment contract", () => {
  it.each(valid)("accepts the saved %s without recalculating a rubric grade", (_name, resolution, row) => {
    expect(confirmedJudgment(response(row), 7, { resolution, reason, level: resolution === "mark_level" ? 1 : undefined })).toBe(row);
  });
  it.each([
    ["ungraded pass", "mark_pass", { score: null }],
    ["ungraded fail", "mark_fail", { outcome: "failed", score: null }],
    ["zero binary pass", "mark_pass", { score: 0 }],
    ["graded fail", "mark_fail", { outcome: "failed", score: 100 }],
    ["partial explicit full pass", "mark_pass", { score: 50 }],
    ["retained level on binary action", "mark_pass", { level }],
    ["ungraded named level", "mark_level", { outcome: "not_applicable", score: null, level }],
    ["failed named level", "mark_level", { outcome: "failed", score: 33.33, level }],
    ["negative named grade", "mark_level", { score: -1, level }],
    ["invalid level points", "mark_level", { score: 33.33, level: { ...level, points: Number.NaN } }],
    ["ungraded majority", "accept_majority", { outcome: "not_applicable", score: null }],
    ["null majority grade", "accept_majority", { score: null }],
    ["out-of-range majority", "accept_majority", { score: 1000 }],
  ] as const)("rejects %s before receipt construction", (_name, resolution, fields) => {
    const row = record(resolution, fields);
    expect(confirmedJudgment(response(row), 7, { resolution, reason, level: resolution === "mark_level" ? 1 : undefined })).toBeUndefined();
  });
  it("rejects a different saved reason and ambiguous criterion identity", () => {
    const row = record("mark_pass");
    expect(confirmedJudgment(response(row), 7, { resolution: "mark_pass", reason: "A different private reason." })).toBeUndefined();
    const out = response(row); out.report.results.push({ ...row });
    expect(confirmedJudgment(out, 7, { resolution: "mark_pass", reason })).toBeUndefined();
  });
});
