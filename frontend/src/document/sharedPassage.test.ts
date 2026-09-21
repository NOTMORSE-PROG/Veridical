import { describe, expect, it } from "vitest";
import { sharedPassageRanges } from "./sharedPassage";

describe("sharedPassageRanges", () => {
  it("identifies the actual contiguous wording shared by both passages", () => {
    const left = "The system uses a hybrid rule-based and AI approach for review.";
    const right = "In this project, the system uses a hybrid rule-based and AI approach during review.";
    const ranges = sharedPassageRanges(left, right);

    expect(left.slice(ranges.left[0].start, ranges.left[0].end)).toBe("The system uses a hybrid rule-based and AI approach");
    expect(right.slice(ranges.right[0].start, ranges.right[0].end)).toBe("the system uses a hybrid rule-based and AI approach");
  });

  it("does not invent an exact highlight for semantic similarity without a stable shared phrase", () => {
    const ranges = sharedPassageRanges(
      "The prototype was assessed by twelve instructors.",
      "A dozen faculty members evaluated the application.",
    );
    expect(ranges).toEqual({ left: [], right: [] });
  });

  it("can mark more than one non-overlapping shared run", () => {
    const left = "Alpha beta gamma differs here, while delta epsilon zeta ends it.";
    const right = "Alpha beta gamma changes there, but delta epsilon zeta ends it.";
    const ranges = sharedPassageRanges(left, right);
    expect(ranges.left).toHaveLength(2);
    expect(ranges.right).toHaveLength(2);
  });
});
