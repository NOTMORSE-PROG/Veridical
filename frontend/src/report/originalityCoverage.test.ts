import { describe, expect, it } from "vitest";
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { originalityNotComparedDetail, pageSpan } from "./originalityCoverage";
import { coverageItems } from "./reportCoverage";

const status = (overrides: Partial<IntegrityCheckStatusOut>): IntegrityCheckStatusOut => ({
  check_kind: "originality_reuse", outcome: "escalated", n_skipped_quota: 0, n_skipped_api_down: 0, n_skipped_parse_failure: 0, ...overrides,
});

describe("originality text left out of the comparison", () => {
  it("names pages as ranges in plain words", () => {
    expect(pageSpan(Array.from({ length: 31 }, (_, i) => 157 + i))).toBe("pages 157 to 187");
    expect(pageSpan([3, 5, 9, 10, 11, 12])).toBe("pages 3, 5 and 9 to 12");
    expect(pageSpan([7])).toBe("page 7");
  });

  it("says what was not compared and that a rerun gives the same answer, for PDF pages and for Word", () => {
    expect(originalityNotComparedDetail(status({ coverage_reason: "reference_end_unclear", pages_not_compared: [157, 158, 159] })))
      .toBe("The end of the reference list could not be found, so pages 157 to 159 (from where the list starts to the end) were not compared. Check any appendices there yourself. Running the check again gives the same result.");
    expect(originalityNotComparedDetail(status({ coverage_reason: "reference_end_unclear", pages_not_compared: [], not_compared_from: "\u00b6212" })))
      .toContain("everything from \u00b6212 to the end of the document was not compared");
    expect(originalityNotComparedDetail(status({ coverage_reason: "reference_boundary_unclear", pages_not_compared: [1, 2, 3] })))
      .toBe("The body could not be told apart from the reference list, so no text was compared (pages 1 to 3). This is not evidence of originality. Running the check again gives the same result.");
  });

  it("never offers Run again or says unverifiable for these limits", () => {
    for (const coverage_reason of ["reference_end_unclear", "reference_boundary_unclear", "reference_membership_partial"]) {
      const [item] = coverageItems({ manuscript_id: 4, check_run_id: 9, integrity_check_status: [status({ coverage_reason, pages_not_compared: [5] })] } as unknown as ReportOut);
      expect(item.action).toBeUndefined();
      expect(`${item.label} ${item.detail}`).not.toMatch(/unverifiable/i);
      expect(item.label).toMatch(/^Originality\/reuse · (Not|Partly) compared$/);
    }
  });

  it("says nothing for other checks or reasons", () => {
    expect(originalityNotComparedDetail(status({ check_kind: "citation_integrity", coverage_reason: "reference_end_unclear" }))).toBeNull();
    expect(originalityNotComparedDetail(status({ coverage_reason: "something_else" }))).toBeNull();
  });
});
