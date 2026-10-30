import { describe, expect, it } from "vitest";
import type { ReportOut } from "../api/types";
import { needsReviewReason } from "./readinessHeadline";

const row = (outcome: string) => ({ outcome }) as ReportOut["results"][number];
const base = {
  status: "needs_review", pending_review_count: 0, results: Array.from({ length: 9 }, () => row("passed")),
  integrity_modules: [
    { check_kind: "internal_agreement", state: "partial" }, { check_kind: "citation_integrity", state: "partial" },
    { check_kind: "statistical_forensics", state: "partial" }, { check_kind: "originality_reuse", state: "partial" },
  ],
  integrity_check_status: [
    { source_pages_unassessed: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12] }, { source_pages_unassessed: [3, 4, 5] },
  ],
} as unknown as ReportOut;

describe("the Needs Review headline reason", () => {
  it("names partial checks, unread pages once, and grades", () => {
    expect(needsReviewReason(base)).toBe("4 checks could not read 10 pages · 9 of 9 criteria graded");
  });
  it("adds criteria still waiting and says a check ran in part when pages are unknown", () => {
    expect(needsReviewReason({ ...base, pending_review_count: 2, integrity_check_status: [],
      integrity_modules: [{ check_kind: "citation_integrity", state: "incomplete" }],
      results: [row("passed"), row("escalated"), row("escalated")] } as ReportOut))
      .toBe("1 check ran only in part · 1 of 3 criteria graded · 2 waiting for you");
  });
  it("says nothing for other bands", () => {
    expect(needsReviewReason({ ...base, status: "ready" } as ReportOut)).toBeNull();
  });
  it("names a check that compared no text instead of counting it as run in part", () => {
    expect(needsReviewReason({ ...base, originality_scope: { not_compared_reason: "reference_boundary_unclear" },
      integrity_check_status: [{ check_kind: "originality_reuse", coverage_reason: "reference_boundary_unclear", pages_not_compared: [1, 2] }],
    } as unknown as ReportOut)).toBe("3 checks ran only in part · 1 check could not compare any text · 9 of 9 criteria graded");
  });
});

