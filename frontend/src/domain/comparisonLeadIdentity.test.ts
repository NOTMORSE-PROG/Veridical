import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { buildReviewFindings } from "../document/reviewFindings";
import { clusterFlagFindings } from "../report/flagClusters";
import { comparisonLeadIdentity, comparisonLeadSnapshot, isOptionalComparisonLead } from "./comparisonLeadIdentity";

function flag(id: number, patch: Partial<FlagSummaryOut> = {}): FlagSummaryOut {
  return { id, check_kind: "originality_reuse", affects_readiness: false, severity: "high", criterion_text: null,
    evidence_excerpt: "Same comparison text", page_anchor: "paragraph 2", overridden: false, is_passage_level: true,
    first_upload_context: false, confirmed_citation_source: false, problem_kind: "reuse_high_similarity_passage", matched_ref: 13, ...patch };
}
describe("optional comparison record identity", () => {
  it("keeps 31 stored records distinct across both existing grouping consumers", () => {
    const records = Array.from({ length: 31 }, (_, i) => flag(i + 39));
    expect(buildReviewFindings(records)).toHaveLength(31);
    expect(clusterFlagFindings(records)).toHaveLength(31);
    expect(buildReviewFindings(records).every((finding) => finding.flags.length === 1)).toBe(true);
  });
  it.each([true, undefined, null])("does not infer optional status from readiness=%s", (affects_readiness) => {
    expect(isOptionalComparisonLead({ ...flag(1), affects_readiness })).toBe(false);
  });
  it("does not rename a non-F7 non-scoring record", () => {
    expect(isOptionalComparisonLead(flag(1, { check_kind: "internal_agreement" }))).toBe(false);
  });
  it("assigns stable separate ordinals before filter, disposition or API ordering", () => {
    const records = [flag(42), flag(40, { overridden: true }), flag(41), flag(1, { check_kind: "internal_agreement" })];
    const snapshot = comparisonLeadSnapshot(5, records);
    expect(snapshot.records.map((record) => record.id)).toEqual([40, 41, 42]);
    expect(comparisonLeadIdentity(flag(41), snapshot)).toEqual({ label: "Comparison lead 2 of 3. Record #41.", marker: "L2" });
    expect(comparisonLeadIdentity(flag(41), comparisonLeadSnapshot(5, [...records].reverse()))).toEqual(comparisonLeadIdentity(flag(41), snapshot));
  });
  it("never invents an ordinal from a standalone or absent record", () => {
    expect(comparisonLeadIdentity(flag(41))).toEqual({ label: "Record #41", marker: "Record #41" });
    expect(comparisonLeadIdentity(flag(41), comparisonLeadSnapshot(5, [flag(40)]))).toEqual({ label: "Record #41", marker: "Record #41" });
  });
});
