import { describe, expect, it } from "vitest";
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { coverageItems } from "./reportCoverage";

const base: IntegrityCheckStatusOut = {
  check_kind: "statistical_forensics", outcome: "escalated", n_skipped_quota: 0,
  n_skipped_api_down: 0, n_skipped_parse_failure: 0,
};
const project = (status: IntegrityCheckStatusOut) => coverageItems({
  manuscript_id: 12, check_run_id: 17, integrity_check_status: [status],
} as ReportOut);

describe("statistical notation assessment limits", () => {
  it.each(["partial", "unsupported"] as const)("points %s notation to the manuscript without a self-link or another run", (state) => {
    const rows = project({ ...base, coverage_reason: "statistical_notation_unassessed",
      statistical_notation_unassessed: true, analysis_coverage: state, assessment_scope: "available_text_and_tables" });
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toContain(state === "partial" ? "Partially assessed" : "Not assessed");
    expect(rows[0].detail).toContain("Check the original statements in the manuscript");
    expect(rows[0].action).toBeUndefined();
    expect(rows[0].subIssues).toContain("This limit alone does not show that a statistic is wrong.");
    expect(JSON.stringify(rows)).not.toContain("Run again");
  });

  it("retains the image-focused action and actual image source links", () => {
    const image: IntegrityCheckStatusOut = { ...base, coverage_reason: "image_content_unassessed",
      assessment_scope: "available_text_and_tables", analysis_coverage: "unsupported",
      n_images_unassessed: 1, image_coverage_items: [{ image_index: 0, page: 4, paragraph: null, reason: "reading_not_recorded" }] };
    const before = project(image)[0];
    const after = project({ ...image, statistical_notation_unassessed: true });
    expect(after).toHaveLength(1);
    expect(after[0].action).toEqual(before.action);
    expect(after[0].sources).toEqual(before.sources);
    expect(after[0].subIssues?.join(" ")).toContain("Statistical notation was found");
    expect(after[0].subIssues?.filter(value => value === "This limit alone does not show that a statistic is wrong.")).toHaveLength(1);
  });

  it.each([null, false, undefined])("does not interpret historical/empty %s as observed unsupported notation", (state) => {
    const original = project({ ...base, coverage_reason: "image_content_unassessed",
      assessment_scope: "available_text_and_tables", analysis_coverage: "unsupported" });
    const current = project({ ...base, coverage_reason: "image_content_unassessed",
      assessment_scope: "available_text_and_tables", analysis_coverage: "unsupported",
      statistical_notation_unassessed: state });
    expect(current).toEqual(original);
  });
});
