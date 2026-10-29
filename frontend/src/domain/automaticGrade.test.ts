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

  it("says nothing for rows the AI did not grade", () => {
    expect(automaticGradeLines({})).toEqual([]);
    expect(automaticGradeLines({ support_caution: "orphaned caution" })).toEqual([]);
    expect(automaticGradeCaution({ support_caution: "orphaned caution" })).toBeNull();
  });

  it("lists what each grading pass chose, so a disagreement is visible", () => {
    expect(automaticGradePasses({ votes: ["Proficient", "Proficient", "Beginner"] })).toBe("Grading passes: Pass 1 Proficient, Pass 2 Proficient, Pass 3 Beginner.");
    expect(automaticGradePasses({ votes: ["Exemplary", null] })).toBe("Grading passes: Pass 1 Exemplary, Pass 2 no usable grade.");
    expect(automaticGradePasses({ votes: [] })).toBeNull();
    expect(automaticGradePasses({})).toBeNull();
  });
});
