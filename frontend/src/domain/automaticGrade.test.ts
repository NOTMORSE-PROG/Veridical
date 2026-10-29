import { describe, expect, it } from "vitest";
import { AUTOMATIC_GRADE_COPY, automaticGradeLines } from "./automaticGrade";

describe("automaticGradeLines", () => {
  it("states how many grading passes chose the grade", () => {
    expect(automaticGradeLines({ agreeing_passes: 2, grading_passes: 3 })).toEqual([
      "Graded automatically: 2 of 3 grading passes chose this grade.",
    ]);
  });

  it("names the rule used when the passes did not simply agree, with any caution", () => {
    expect(automaticGradeLines({ decision_basis: "middle_of_three", agreeing_passes: 1, grading_passes: 3,
      support_caution: "The quote shows the topic but not the preview." })).toEqual([
      AUTOMATIC_GRADE_COPY.middle_of_three,
      "Evidence check not confirmed: The quote shows the topic but not the preview.",
    ]);
  });

  it("says nothing for rows the AI did not grade", () => {
    expect(automaticGradeLines({})).toEqual([]);
    expect(automaticGradeLines({ support_caution: "orphaned caution" })).toEqual([]);
  });
});
