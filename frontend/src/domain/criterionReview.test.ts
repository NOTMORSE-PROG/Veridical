import { describe, expect, it } from "vitest";
import type { EscalatedItemOut } from "../api/types";
import { criterionReviewNotices } from "./criterionReview";

function item(patch: Partial<EscalatedItemOut> = {}): EscalatedItemOut {
  return {
    check_result_id: 8, criterion_id: 3, criterion_text: "An instructor-defined requirement",
    weight: 1, agreement: null, votes: [], ai_majority_verdict: null,
    reason: null, review_reason: "low_confidence", unverified_evidence: null,
    ...patch,
  };
}

describe("criterion review interpretation", () => {
  it.each(["support_incomplete", "support_unconfirmed"] as const)("retains the explicit %s code when optional metadata is absent", (review_reason) => {
    const notices = criterionReviewNotices(item({ review_reason, ai_majority_verdict: "pass" }));
    expect(notices).toHaveLength(1);
    expect(notices[0].kind).toBe("support");
    expect(notices[0].text).toContain(review_reason === "support_incomplete" ? "did not finish" : "could not confirm");
  });
  const resultShapes: { votes: (string | null)[] }[] = [
    { votes: [] }, { votes: [null, null] },
    { votes: ["Proficient", null] }, { votes: ["Proficient", "Developing"] },
  ];
  it.each(resultShapes)(
    "does not infer absent analysis or disagreement from result entries $votes", ({ votes }) => {
      expect(criterionReviewNotices(item({ votes, reasoning: "A standalone recorded explanation" }))).toEqual([]);
    },
  );

  it.each(resultShapes)(
    "keeps source-instruction caution without assuming agreement for $votes", ({ votes }) => {
      const notices = criterionReviewNotices(item({ votes, injection_suspected: true }));
      expect(notices).toEqual([{ kind: "source_instructions", text: "Text that may be directed at automated grading needs review." }]);
    },
  );

  it("retains simultaneous support, source-instruction and unrecognized-grade limitations without a snippet", () => {
    const notices = criterionReviewNotices(item({
      support_withheld: true, support_status: "incomplete", review_reason: "support_incomplete",
      injection_suspected: true, injection_matched_snippet: null, verdict_unrecognized: true,
      ai_majority_verdict: "Unmapped grade",
    }));
    expect(notices.map((notice) => notice.kind)).toEqual(["support", "source_instructions", "unrecognized_grade"]);
    expect(notices.map((notice) => notice.text).join(" ")).not.toMatch(/agreed|split|failed criterion|never ran/);
  });

  it.each(["incomplete", "unsupported", "uncertain", null] as const)(
    "keeps evidence-support status %s distinct from manuscript failure", (support_status) => {
      const notices = criterionReviewNotices(item({ support_withheld: true, support_status }));
      expect(notices).toHaveLength(1);
      expect(notices[0].kind).toBe("support");
      expect(notices[0].text).not.toMatch(/manuscript failed|meets criterion|No grade was accepted/);
    },
  );

  it("does not derive a completion cause or request count from recorded system prose", () => {
    const result = item({ review_reason: "not_graded", reason: "Batch was interrupted after a request", votes: [null] });
    const before = structuredClone(result);
    expect(criterionReviewNotices(result)).toEqual([{ kind: "not_graded", text: "A completed grade is not available in this review record." }]);
    expect(result).toEqual(before);
  });

  it("does not manufacture agreement counts for whitespace-equivalent suggestions", () => {
    const notices = criterionReviewNotices(item({ votes: ["Proficient", " Proficient "], ai_majority_verdict: "Proficient", agreement: 1 }));
    expect(notices).toEqual([{ kind: "tentative_suggestion", text: "A tentative AI suggestion is recorded. Review it against the criterion and manuscript." }]);
  });
});
