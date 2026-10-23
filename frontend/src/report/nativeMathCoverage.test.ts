import { describe, expect, it } from "vitest";
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { coverageItems } from "./reportCoverage";

const status: IntegrityCheckStatusOut = {
  check_kind: "statistical_forensics", outcome: "escalated", coverage_reason: "native_math_unassessed",
  n_skipped_quota: 0, n_skipped_api_down: 0, n_skipped_parse_failure: 0,
  assessment_scope: "available_text_and_tables", analysis_coverage: "partial",
  native_math_inventory_state: "complete", n_native_equations_unassessed: 1,
  native_math_coverage_items: [{ paragraph: 5, kind: "paragraph", n_occurrences: 1 }],
};
function items(patch: Partial<IntegrityCheckStatusOut> = {}) {
  return coverageItems({ manuscript_id: 2, check_run_id: 3, integrity_check_status: [{ ...status, ...patch }] } as ReportOut);
}

describe("recorded native Word coverage", () => {
  it("projects one F6 limitation with an actual container link and native occurrence units", () => {
    const [item] = items();
    expect(item.detail).toContain("supported statistical statements");
    expect(item.subIssues?.join(" ")).toContain("1 native Word equation occurrence remains unassessed");
    expect(item.sources?.[0].to).toBe("/report/3/document?anchor=paragraph%205&coverage=native_math");
    expect(item.subIssues?.join(" ")).not.toContain("transcribed");
  });

  it("combines native and image limitations without turning either into a passed equation", () => {
    const result = items({ coverage_reason: "image_content_unassessed", n_equations_unassessed: 2,
      n_images_unassessed: 1, image_coverage_items: [{ image_index: 0, page: null, paragraph: 7, reason: "api_down" }] });
    expect(result).toHaveLength(1);
    expect(result[0].subIssues?.join(" ")).toContain("2 equations were transcribed from images but not evaluated");
    expect(result[0].subIssues?.join(" ")).toContain("1 native Word equation occurrence remains");
    expect(result[0].sources).toHaveLength(2);
    expect(result[0].sources?.[1].label).toContain("service was unavailable");
  });

  it("keeps unsupported, unknown count and unlocated occurrence meanings separate", () => {
    expect(items({ analysis_coverage: "unsupported" })[0].detail).toContain("does not mean the manuscript contains no statistics");
    const unknown = items({ coverage_reason: "native_math_inventory_unavailable", native_math_inventory_state: "not_recorded", n_native_equations_unassessed: null, native_math_coverage_items: [] })[0];
    expect(unknown.subIssues?.join(" ")).toContain("presence and count are unknown");
    expect(unknown.subIssues?.join(" ")).not.toContain("0 native");
    expect(items({ native_math_coverage_items: [{ paragraph: null, kind: "table_cell", n_occurrences: 1 }] })[0].sources?.[0].to).toBeNull();
  });

  it("keeps a partial citation scan distinct from a statistical assessment", () => {
    const [item] = items({ check_kind: "citation_integrity", coverage_reason: "native_math_citation_incomplete",
      assessment_scope: "available_text_and_reference_metadata", n_linkage_unassessed: 3 });
    expect(item.label).toContain("Citation integrity");
    expect(item.detail).toContain("cannot establish that a reference is unused");
    expect(item.subIssues).toContain("3 reference links unassessed");
    expect(item.detail).not.toContain("statistical");
  });
});
