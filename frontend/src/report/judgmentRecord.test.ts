import { describe, expect, it } from "vitest";
import type { JudgmentSnapshot } from "../api/judgments";
import type { CriterionResultOut } from "../api/types";
import { atLeastSavedJudgment, sameSavedJudgment } from "./savedJudgment";
import { sameJudgmentGrade, snapshotValid } from "./judgmentRecord";

const current: JudgmentSnapshot = { judgment_revision: 1, resolution_type: "mark_pass", outcome: "passed", score: 100,
  level: null, reason: "Recorded source-based instructor reason", actor_id: 1, recorded_at: "2026-10-23T01:00:00Z", provenance: "recorded" };
const row = (version: number, reason = current.reason, result = 41): CriterionResultOut => ({
  criterion_id: 101, check_result_id: result, judgment_revision: version,
  text: "A custom criterion", type: "semantic", kind: "semantic", weight: 1, weight_importance: "med",
  outcome: "passed", score: 100, basis: null, anchor: null, reason: null, reasoning: null, evidence: [], level: null,
  resolution: { type: "mark_pass", reason, ai_majority_verdict: null },
});

describe("Judgment version and fact boundaries", () => {
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
