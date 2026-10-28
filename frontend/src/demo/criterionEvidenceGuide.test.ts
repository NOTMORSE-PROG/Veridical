import { describe, expect, it } from "vitest";
import { criterionEvidenceGuide } from "./criterionEvidenceGuide";
import { sampleReport } from "./workspaceFixture";

describe("recorded criterion source guide", () => {
  it("accounts for every saved passage for all four criteria", () => {
    for (const row of sampleReport.results) {
      const guide = criterionEvidenceGuide(1, row.criterion_id, row.text, row.evidence)!;
      expect(guide).not.toBeNull();
      expect([...new Set(guide.flatMap(group => group.indexes))].sort((a, b) => a - b)).toEqual(row.evidence.map((_, index) => index));
    }
  });
  it("does not invent a reasoning map for another run, title, partial snapshot or changed quotation", () => {
    const row = sampleReport.results[0];
    expect(criterionEvidenceGuide(2, row.criterion_id, row.text, row.evidence)).toBeNull();
    expect(criterionEvidenceGuide(1, row.criterion_id, "Another criterion", row.evidence)).toBeNull();
    expect(criterionEvidenceGuide(1, row.criterion_id, row.text, row.evidence.slice(0, 5))).toBeNull();
    expect(criterionEvidenceGuide(1, row.criterion_id, row.text, [{ ...row.evidence[0], quote: "Changed statement" }, ...row.evidence.slice(1)])).toBeNull();
  });
  it("qualifies the empty objective as a visual check rather than a quotation of absent text", () => {
    const row = sampleReport.results[3];
    expect(criterionEvidenceGuide(1, row.criterion_id, row.text, row.evidence)![1].note).toContain("not an exact quotation of missing text");
  });
});
