import { describe, expect, it } from "vitest";
import { sampleFlags, sampleReport } from "../demo/workspaceFixture";
import { publicEvidenceImages } from "../demo/sourceEvidence";
import { associateSharedEvidence, sharedGradeLabel } from "./sharedEvidence";

describe("public evidence association", () => {
  it("keeps all 18 roles attached to their explicit criterion or finding", () => {
    const grouped = associateSharedEvidence(publicEvidenceImages(), sampleReport, sampleFlags.map(flag => ({ ...flag, problem_kind: flag.ai_verdict_summary })));
    expect([...grouped.criteria.values()].map(items => items.length)).toEqual([5, 3, 1, 1]);
    expect([...grouped.findings.values()].map(items => items.length)).toEqual([2, 2, 2, 2]);
    expect(grouped.additional).toEqual([]);
  });
  it("retains unassigned, wrong-subject and changed-quotation evidence without guessing from labels", () => {
    const image = publicEvidenceImages()[0];
    const images = [{ ...image, subject: null }, { ...image, subject: { kind: "criterion" as const, id: 99 } }, { ...image, quote: "Different quotation" }];
    const grouped = associateSharedEvidence(images, sampleReport, sampleFlags.map(flag => ({ ...flag, problem_kind: flag.ai_verdict_summary })));
    expect(grouped.criteria.size).toBe(0); expect(grouped.findings.size).toBe(0);
    expect(grouped.additional).toEqual(images);
  });
  it("never presents a stale positive level for an unassessed criterion", () => {
    expect(sharedGradeLabel({ ...sampleReport.results[0], outcome: "api_down", level: { name: "Above Average", ordinal: 4, points: 2, max_points: 2 } })).toBe("Not assessed: service unavailable");
  });
});
