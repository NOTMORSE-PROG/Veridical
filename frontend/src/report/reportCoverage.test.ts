import { describe, expect, it } from "vitest";
import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import { coverageItems } from "./reportCoverage";

const status: IntegrityCheckStatusOut = {
  check_kind: "citation_integrity", outcome: "escalated", n_skipped_quota: 0,
  n_skipped_api_down: 0, n_skipped_parse_failure: 0,
  coverage_reason: "source_reading_order_partial", analysis_coverage: "partial",
  assessment_scope: "reference_metadata_only", n_reference_metadata_attempted: 3,
  n_metadata_skipped_insufficient: 8, n_source_pages_unassessed: 5,
};

function item(patch: Partial<IntegrityCheckStatusOut> = {}) {
  return coverageItems({
    manuscript_id: 2, check_run_id: 3, integrity_check_status: [{ ...status, ...patch }],
  } as ReportOut)[0];
}

describe("partial source coverage", () => {
  it("does not confuse metadata checks with full citation or claim assessment", () => {
    const row = item();
    expect(row.label).toContain("Partially assessed");
    expect(row.detail).toContain("Citation matching across the manuscript was not assessed");
    expect(row.subIssues?.join(" ")).toContain("3 reference checks attempted");
    expect(row.subIssues?.join(" ")).toContain("Claim support across the manuscript was not assessed");
    expect(row.subIssues?.join(" ")).not.toContain("24 pages");
    expect(row.action?.to).toBe("/report/3/document?view=decision");
  });

  it("does not call extraction without a lookup a partial source verification", () => {
    const row = item({ analysis_coverage: "unsupported", n_reference_metadata_attempted: 0 });
    expect(row.label).toContain("Not assessed");
    expect(row.detail).toContain("their details could not be checked");
    expect(row.subIssues?.join(" ")).toContain("0 reference checks attempted");
  });

  it("discloses provider failure separately from claim-support failure", () => {
    expect(item({ n_metadata_api_down: 2 }).subIssues?.join(" ")).toContain(
      "2 source checks could not reach an external service",
    );
  });

  it("retains legacy full-stop wording without inferring completed coverage", () => {
    const row = item({ assessment_scope: null, analysis_coverage: null,
      coverage_reason: "source_reading_order_unresolved" });
    expect(row.detail).toContain("This check was not assessed");
  });
});
