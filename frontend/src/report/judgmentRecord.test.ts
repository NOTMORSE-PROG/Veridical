import { describe, expect, it } from "vitest";
import type { JudgmentContext, JudgmentHistoryEntry, JudgmentRecordInput, JudgmentRevisionOutput, JudgmentSnapshot } from "../api/judgments";
import type { CriterionResultOut, ReportOut } from "../api/types";
import { atLeastSavedJudgment, sameSavedJudgment } from "./savedJudgment";
import { contextInReport, contextMatches, historyEntryMatches, originalAssessmentValid, recordAcknowledged, sameJudgmentGrade, snapshotValid } from "./judgmentRecord";

const current: JudgmentSnapshot = { judgment_revision: 1, resolution_type: "mark_pass", outcome: "passed", score: 100,
  level: null, reason: "Recorded source-based instructor reason", actor_id: 1, recorded_at: "2026-10-23T01:00:00Z", provenance: "recorded" };
const row = (version: number, reason = current.reason, result = 41): CriterionResultOut => ({
  criterion_id: 101, check_result_id: result, judgment_revision: version,
  text: "A custom criterion", type: "semantic", kind: "semantic", weight: 1, weight_importance: "med",
  outcome: "passed", score: 100, basis: null, anchor: null, reason: null, reasoning: null, evidence: [], level: null,
  resolution: { type: "mark_pass", reason, ai_majority_verdict: null },
});

function firstJudgment() {
  const bound: JudgmentContext = { check_run_id: 5, criterion_id: 101, check_result_id: 41,
    criterion_text: "A custom criterion", assessment_revision: 0, decision: null, can_record: true, can_revise: false,
    unavailable_reason: null, current: null, choices: { kind: "pass_fail", levels: [], exclusion_allowed: true, unavailable_reason: null },
    original_assessment: { kind: "semantic", basis: "llm", reason: null, reasoning: "Original saved basis", ai_majority_verdict: "pass",
      evidence: [], unverified_evidence: [], support_withheld: false, support_status: null, evidence_unavailable: false,
      original_outcome: "passed", original_score: 100, original_level: null } };
  const submitted: JudgmentRecordInput = { expected_revision: 0, resolution: "mark_pass", reason: current.reason };
  const context: JudgmentContext = { ...bound, assessment_revision: 1, can_record: false, can_revise: true, current };
  const entry: JudgmentHistoryEntry = { schema_version: 2, entry_id: 11, check_run_id: 5, criterion_id: 101, check_result_id: 41,
    action: "judgment_recorded", assessment_revision_before: 0, assessment_revision_after: 1, before: null, after: current,
    performed_by: 1, performed_at: current.recorded_at!, original_assessment: bound.original_assessment };
  const out: JudgmentRevisionOutput = { context, entry,
    report: { check_run_id: 5, assessment_revision: 1, decision: null, results: [row(1)] } as ReportOut };
  return { bound: structuredClone(bound), submitted: structuredClone(submitted), out: structuredClone(out) };
}

describe("Initial judgment acknowledgment and history", () => {
  it("accepts a separately bound original and a null manual before without depending on JSON key order", () => {
    const { bound, submitted, out } = firstJudgment();
    const reordered = Object.fromEntries(Object.entries(out.context.original_assessment).reverse()) as JudgmentContext["original_assessment"];
    out.context.original_assessment = reordered;
    if (out.entry.schema_version === 2) out.entry.original_assessment = reordered;
    expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(true);
    expect(historyEntryMatches(out.entry, out.context)).toBe(true);
  });

  it.each(["manual_before", "wrong_schema", "wrong_result", "changed_original", "wrong_actor", "wrong_epoch"])("refuses %s as a confirmed first save", kind => {
    const { bound, submitted, out } = firstJudgment();
    if (kind === "manual_before") out.entry.before = current;
    if (kind === "wrong_schema") Object.assign(out.entry, { schema_version: 1 });
    if (kind === "wrong_result") out.context.check_result_id = 999;
    if (kind === "changed_original") out.context.original_assessment.reasoning = "A different original basis";
    if (kind === "wrong_actor") out.entry.performed_by = 2;
    if (kind === "wrong_epoch") out.report.assessment_revision = 2;
    expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(false);
  });

  it("does not bind initial authority to a row already carrying a manual token", () => {
    const { bound, out } = firstJudgment();
    expect(contextInReport(bound, { ...out.report, assessment_revision: 0 })).toBe(false);
    bound.can_revise = true;
    expect(contextMatches(bound, 5, 101)).toBe(false);
  });

  it.each([true, "100", NaN, 101])("refuses malformed original numeric fact %j", value => {
    const { bound } = firstJudgment();
    Object.assign(bound.original_assessment, { original_score: value });
    expect(originalAssessmentValid(bound.original_assessment)).toBe(false);
    expect(contextMatches(bound, 5, 101)).toBe(false);
  });

  it("does not read a new initial history action through an older schema", () => {
    const { out } = firstJudgment();
    Object.assign(out.entry, { schema_version: 1, original_assessment: null });
    expect(historyEntryMatches(out.entry, out.context)).toBe(false);
  });

  it.each([undefined, true, "missing", []])("refuses unreadable original named-grade data %j without throwing", value => {
    const { bound } = firstJudgment();
    Object.assign(bound.original_assessment, { original_level: value });
    expect(originalAssessmentValid(bound.original_assessment)).toBe(false);
  });

  it("requires the typed history action to match the explicitly submitted choice", () => {
    const { bound, submitted, out } = firstJudgment();
    out.entry.after = { ...out.entry.after, resolution_type: "accept_majority" };
    expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(false);
  });

  it("does not acknowledge an unrelated named grade attached to a binary choice", () => {
    const { bound, submitted, out } = firstJudgment();
    const unexpected = { ordinal: 0, name: "Absent", points: 0, max_points: 2 };
    out.context.current!.level = unexpected;
    out.entry.after.level = structuredClone(unexpected);
    out.report.results[0].level = structuredClone(unexpected);
    expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(false);
    expect(historyEntryMatches(out.entry, out.context)).toBe(false);
  });

  it.each([[0, 100, false], [0, 0, true], [1, 33.33, true], [1, 33.34, false]] as const)(
    "checks named-grade points %s against stored score %s at database precision", (points, score, accepted) => {
      const { bound, submitted, out } = firstJudgment();
      bound.choices = { kind: "levelled", exclusion_allowed: true, unavailable_reason: null,
        levels: [{ level: 0, name: "Chosen grade", points, descriptor: "Source-supported choice" },
          { level: 1, name: "Complete", points: 3, descriptor: "Full support" }] };
      submitted.resolution = "mark_level"; submitted.level = 0;
      out.context.choices = structuredClone(bound.choices);
      const after: JudgmentSnapshot = { ...current, resolution_type: "mark_level", score,
        level: { ordinal: 0, name: "Chosen grade", points, max_points: 3 } };
      out.context.current = after;
      out.entry.after = structuredClone(after);
      out.report.results[0] = { ...row(1), score, level: after.level,
        resolution: { type: "mark_level", reason: current.reason, ai_majority_verdict: null } };
      expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(accepted);
    });
});

describe("Judgment version and fact boundaries", () => {
  it("keeps blank initial comments but requires a new reason for settled judgments and revisions", () => {
    const { out, submitted, bound } = firstJudgment();
    out.entry.after.reason = "";
    out.context.current!.reason = "";
    submitted.reason = "";
    out.report.results[0].resolution!.reason = "";
    expect(recordAcknowledged(out, 5, 101, submitted, bound, 1)).toBe(false);
    expect(historyEntryMatches(out.entry, out.context)).toBe(false);
    const initial: JudgmentHistoryEntry = { ...out.entry, schema_version: 1, action: "initial_resolution", original_assessment: undefined };
    expect(historyEntryMatches(initial, out.context)).toBe(true);
    const after = { ...out.entry.after, judgment_revision: 2, reason: "A new reason for this revision." };
    const revised: JudgmentHistoryEntry = { ...initial, action: "judgment_revised", before: initial.after, after };
    out.context.current = after;
    expect(historyEntryMatches(revised, out.context)).toBe(true);
    revised.after = { ...after, reason: "" };
    expect(historyEntryMatches(revised, out.context)).toBe(false);
  });
  it.each(["mark_pass", "mark_fail", "mark_level"] as const)("recognizes blank ordinary %s comments without inventing a reason", resolution => {
    const blank: JudgmentSnapshot = { ...current, resolution_type: resolution, reason: "",
      outcome: resolution === "mark_fail" ? "failed" : "passed",
      score: resolution === "mark_fail" ? 0 : 100,
      level: resolution === "mark_level" ? { ordinal: 1, name: "Custom top level", points: 1, max_points: 1 } : null };
    expect(snapshotValid(blank)).toBe(true);
    expect(blank.reason).toBe("");
    expect(snapshotValid({ ...blank, resolution_type: "accept_majority" })).toBe(false);
    expect(snapshotValid({ ...blank, resolution_type: "needs_document", outcome: "not_applicable", score: null, level: null })).toBe(false);
  });
  it("lets newer current judgments supersede an old acknowledgment while preserving its immutable value", () => {
    const saved = row(1); const newer = row(2, "A later recorded reason");
    expect(atLeastSavedJudgment(newer, saved)).toBe(true);
    expect(sameSavedJudgment(newer, saved)).toBe(false);
    expect(saved.resolution?.reason).toBe(current.reason);
    expect(atLeastSavedJudgment(row(0), saved)).toBe(false);
    expect(atLeastSavedJudgment(row(1, "A mismatching reason"), saved)).toBe(false);
    expect(atLeastSavedJudgment(row(2, current.reason, 99), saved)).toBe(false);
  });
  it.each([
    { score: "NaN" }, { score: NaN }, { score: 999 }, { score: true },
    { recorded_at: "2026-10-23T01:00:00" }, { actor_id: null }, { judgment_revision: 0 },
    { resolution_type: "needs_document" }, { resolution_type: "mark_level", level: null },
    { level: { ordinal: "0", name: "Zero", points: 0, max_points: 3 } },
    { level: { ordinal: 0, name: "Zero", points: 4, max_points: 3 } },
  ])("rejects malformed purportedly recorded facts %j", change => {
    expect(snapshotValid({ ...current, ...change } as JudgmentSnapshot)).toBe(false);
  });
  it("keeps legitimate partial legacy facts and equal-point named grades distinct", () => {
    const legacy: JudgmentSnapshot = { ...current, judgment_revision: 0, resolution_type: "accept_majority", score: 50,
      actor_id: null, recorded_at: null, provenance: "legacy_partial" };
    expect(snapshotValid(legacy)).toBe(true);
    expect(sameJudgmentGrade(current, legacy)).toBe(false);
    const zero: JudgmentSnapshot = { ...current, resolution_type: "mark_level", score: 0,
      level: { ordinal: 0, name: "Zero grade", points: 0, max_points: 3 } };
    expect(snapshotValid(zero)).toBe(true);
    expect(sameJudgmentGrade(zero, { ...zero, level: { ...zero.level!, ordinal: 1, name: "Other zero grade" } })).toBe(false);
    expect(sameJudgmentGrade(current, { ...current, resolution_type: "accept_majority" })).toBe(true);
  });
});
