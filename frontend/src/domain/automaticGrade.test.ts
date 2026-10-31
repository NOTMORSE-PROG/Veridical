import { describe, expect, it } from "vitest";
import { AUTOMATIC_GRADE_COPY, automaticGradeCaution, automaticGradeLines, automaticGradePasses } from "./automaticGrade";

describe("automaticGradeLines", () => {
  it("states how many grading passes chose the grade", () => {
    expect(automaticGradeLines({ agreeing_passes: 2, grading_passes: 3 })).toEqual([
      "Graded automatically: 2 of 3 grading passes chose this grade.",
    ]);
  });

  it("names the rule used when the passes did not simply agree, and keeps the caution apart", () => {
    const row = { decision_basis: "middle_of_three" as const, agreeing_passes: 1, grading_passes: 3,
      support_caution: "The quote shows the topic but not the preview." };
    expect(automaticGradeLines(row)).toEqual([AUTOMATIC_GRADE_COPY.middle_of_three]);
    expect(automaticGradeCaution(row)).toBe("Evidence check not confirmed: The quote shows the topic but not the preview.");
  });

  it("never says no grade was assigned beside a grade that was kept", () => {
    expect(automaticGradeCaution({ agreeing_passes: 2, grading_passes: 2,
      support_caution: "Review the criterion and its performance level directly; no automatic grade was assigned." }))
      .toBe("Evidence check not confirmed: Review the criterion and its performance level directly.");
  });

  it("does not call a whole-paper grade's caution a failed evidence check", () => {
    const caution = "This grade is about the whole paper, such as its tone, which a few quoted passages cannot show.";
    expect(automaticGradeCaution({ agreeing_passes: 2, grading_passes: 2, support_caution: caution, support_caution_scope: true }))
      .toBe(caution);
    expect(automaticGradeCaution({ agreeing_passes: 2, grading_passes: 2, support_caution: caution }))
      .toBe(`Evidence check not confirmed: ${caution}`);
  });

  it("says nothing for rows the AI did not grade", () => {
    expect(automaticGradeLines({})).toEqual([]);
    expect(automaticGradeLines({ support_caution: "orphaned caution" })).toEqual([]);
    expect(automaticGradeCaution({ support_caution: "orphaned caution" })).toBeNull();
  });

  it("names each pass's grade in the card's words, only when the passes were not all the same", () => {
    expect(automaticGradePasses({ votes: ["Proficient", "Proficient", "Beginner"] }))
      .toBe("What each grading pass chose: Pass 1 Proficient, Pass 2 Proficient, Pass 3 Beginner. A pass is one automatic grading attempt, not an independent reviewer.");
    expect(automaticGradePasses({ votes: ["pass", "fail", "pass"] })).toContain("Pass 1 Meets criterion, Pass 2 Does not meet, Pass 3 Meets criterion.");
    // An abstaining "uncertain" pass reads the same as a pass with no grade.
    expect(automaticGradePasses({ votes: ["Exemplary", null, "uncertain"] })).toContain("Pass 2 no grade recorded, Pass 3 no grade recorded.");
    expect(automaticGradePasses({ votes: ["Proficient", "Proficient"] })).toBeNull();
    expect(automaticGradePasses({ votes: [] })).toBeNull();
    expect(automaticGradePasses({})).toBeNull();
  });
});
