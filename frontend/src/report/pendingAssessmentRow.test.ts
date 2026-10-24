import { expect, it } from "vitest";
import type { CriterionResultOut, EscalatedItemOut, ReportOut } from "../api/types";
import { pendingAssessmentRow } from "./pendingAssessmentRow";

const row = { criterion_id: 2, check_result_id: 10, outcome: "escalated", resolution: null } as CriterionResultOut;
const report = { check_run_id: 1, assessment_revision: 4, results: [row] } as ReportOut;
const item = { criterion_id: 2, check_result_id: 10, assessment_revision: 4 } as EscalatedItemOut;

it("joins a unique unresolved criterion only within its saved assessment", () => {
  expect(pendingAssessmentRow(report, item, 1)).toBe(row);
});
it.each([undefined, null, -1, true, "4", 1.5, Number.NaN, Infinity])("rejects missing or invalid epochs %s on either response", value => {
  expect(pendingAssessmentRow({ ...report, assessment_revision: value } as ReportOut, item, 1)).toBeUndefined();
  expect(pendingAssessmentRow(report, { ...item, assessment_revision: value } as EscalatedItemOut, 1)).toBeUndefined();
});
it("does not equate legacy null epochs or borrow a stale assessment", () => {
  expect(pendingAssessmentRow({ ...report, assessment_revision: null }, { ...item, assessment_revision: null }, 1)).toBeUndefined();
  expect(pendingAssessmentRow(report, { ...item, assessment_revision: 3 }, 1)).toBeUndefined();
  expect(pendingAssessmentRow(report, item, 99)).toBeUndefined();
  expect(pendingAssessmentRow(undefined, item, 1)).toBeUndefined();
});
it.each([
  [], [row, row], [{ ...row, criterion_id: 3 }], [{ ...row, check_result_id: 11 }],
  [row, { ...row, check_result_id: 11 }], [row, { ...row, criterion_id: 3 }],
  [{ ...row, resolution: { type: "mark_pass", reason: "Instructor reason", ai_majority_verdict: null } }],
  [{ ...row, outcome: "passed" }], [{ ...row, outcome: "failed" }], [{ ...row, outcome: "not_applicable" }],
].map(results => ({ results }))) ("rejects ambiguous, mismatched or no-longer-pending rows %#", ({ results }) => {
  expect(pendingAssessmentRow({ ...report, results } as ReportOut, item, 1)).toBeUndefined();
});
it.each(["escalated", "unverifiable", "api_down", "quota_exhausted"] as const)("preserves the genuine pending outcome %s", outcome => {
  expect(pendingAssessmentRow({ ...report, results: [{ ...row, outcome }] }, item, 1)?.outcome).toBe(outcome);
});
