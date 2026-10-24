import { expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { criterionAssessmentDetails } from "./criterionAssessment";
import { unconfirmedAutomaticRuleRows } from "./ruleScope";

const row: ResultRowCommon = { criterion_id: 1, text: "Abstract includes the study purpose", type: "structural", kind: "structural", basis: null,
  weight: 1, weight_importance: "med", outcome: "passed", score: 100, reasoning: null, reason: null, anchor: null, evidence: [],
  rule_scope_unconfirmed: true, rule_facts: { rule_id: "required_section_present", target: "abstract", matched_title: "Abstract" } };

it("qualifies only explicitly marked, valid heading records with a known rule method", () => {
  expect(criterionAssessmentDetails(row, false).ruleScopeUnconfirmed).toBe(true);
  for (const patch of [{ rule_scope_unconfirmed: false }, { rule_scope_unconfirmed: undefined }, { kind: "semantic" }, { basis: "llm" }, { rule_facts: null }]) {
    expect(criterionAssessmentDetails({ ...row, ...patch } as ResultRowCommon, false).ruleScopeUnconfirmed).toBe(false);
  }
});

it("keeps current instructor judgments out of the band qualification while retaining earlier-record context", () => {
  const manual = { ...row, resolution: { type: "mark_pass" as const, reason: "Verified the complete requirement.", ai_majority_verdict: null } };
  expect(criterionAssessmentDetails(manual, false).ruleScopeUnconfirmed).toBe(true);
  expect(unconfirmedAutomaticRuleRows([manual])).toEqual([]);
  expect(unconfirmedAutomaticRuleRows([row, { ...row, outcome: "escalated" }])).toEqual([row]);
});

it("uses only the public resolved fact when qualifying a shared band", () => {
  const injected = { ...row, resolution: { type: "mark_pass" as const, reason: "Private injected reason", ai_majority_verdict: null } };
  expect(unconfirmedAutomaticRuleRows([injected], true)).toHaveLength(1);
  expect(unconfirmedAutomaticRuleRows([{ ...injected, resolved: true }], true)).toEqual([]);
  expect(unconfirmedAutomaticRuleRows([{ ...row, resolved: true }], false)).toHaveLength(1);
});
