import { describe, expect, it } from "vitest";
import { gradeEvidenceValue } from "./gradeEvidence";

const at = (...anchors: (string | null)[]) => anchors.map((anchor) => ({ anchor }));

describe("quoted evidence line", () => {
  it.each([
    [at(), "none recorded"],
    [at("page 2"), "1 passage (PDF p. 2)"],
    [at("page 3", "page 3"), "2 passages (PDF p. 3)"],
    [at("page 8", "page 8", "page 8", "page 8", "page 8", "page 8", "page 8"), "7 passages (PDF p. 8)"],
    [at("page 3", "page 7", "page 12"), "3 passages (PDF pp. 3, 7, 12)"],
    [at("page 32", "page 33", "page 34", "page 40"), "4 passages (PDF pp. 32-34, 40)"],
    [at("page 3", "page 7", "page 12", "page 40", "page 52", "page 60", "page 70", "page 80", "page 90"), "9 passages (PDF pp. 3, 7, 12, 40, 52 and 4 more)"],
    [at("paragraph 12", "paragraph 13"), "2 passages (extracted paragraphs 13-14)"],
    [at("paragraph 12"), "1 passage (extracted paragraph 13)"],
    [at("page 3", "paragraph 12", "page 3"), "3 passages (PDF p. 3; extracted paragraph 13)"],
    [at("page 3", "page 7", null), "3 passages (PDF pp. 3, 7; 1 with no recorded location)"],
    [at(null), "1 passage (no manuscript location recorded)"],
    [at("", null), "2 passages (no manuscript location recorded)"],
    [at("Appendix B"), "1 passage (Appendix B)"],
  ])("%j reads %s", (evidence, expected) => {
    expect(gradeEvidenceValue(evidence)).toBe(expected);
  });

  it("uses no dashes other than page ranges and no percentages", () => {
    const text = gradeEvidenceValue(at("page 1", "page 2", "page 9"));
    expect(text).not.toMatch(/[—–%]/);
  });
});
