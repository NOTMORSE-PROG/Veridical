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
  } as ReportOut).filter((entry) => entry.key !== "source_pages_unread")[0];
}

describe("partial source coverage", () => {
  it("names each check's unread pages as ranges, and links them once in their own row", () => {
    const value = item({ source_pages_unassessed: [45, 46, 47, 53], n_source_pages_unassessed: 4 });
    expect(value.subIssues?.join(" ")).toContain("Pages 45-47, 53 could not be read reliably, so this check left them out.");
    expect(value.sources ?? []).toEqual([]);
    const statuses = (["internal_agreement", "citation_integrity"] as const).map((check_kind) => ({ ...status, check_kind,
      coverage_reason: "source_reading_order_partial", n_source_pages_unassessed: 4, source_pages_unassessed: [53, 45, 46, 47] }));
    const items = coverageItems({ manuscript_id: 2, check_run_id: 3, integrity_check_status: statuses } as unknown as ReportOut);
    const pages = items.filter((entry) => entry.key === "source_pages_unread");
    expect(pages).toHaveLength(1);
    expect(pages[0].detail).toContain("Pages 45-47, 53 could not be read in a reliable order");
    expect(pages[0].sources).toEqual([
      { label: "Pages 45-47", to: "/report/3/document?anchor=page%2045" },
      { label: "Page 53", to: "/report/3/document?anchor=page%2053" },
    ]);
    expect(items.filter((entry) => entry.sources?.some((source) => source.label === "Page 53"))).toHaveLength(1);
  });
  it("keeps a bare count honest when the saved check did not record which pages", () => {
    const value = item({ source_pages_unassessed: null, n_source_pages_unassessed: 3 });
    expect(value.subIssues?.join(" ")).toContain("3 pages could not be read reliably. This record does not say which");
    expect(value.sources ?? []).toEqual([]);
  });
  it("counts the pages of an originality check that was marked not applicable, and says why nothing was compared", () => {
    const report = { manuscript_id: 2, check_run_id: 3,
      originality_scope: { compared_count: null, corpus: "library", not_compared_reason: "reference_list_unconfirmed" },
      integrity_check_status: [{ ...status, check_kind: "originality_reuse", outcome: "not_applicable", assessment_scope: null,
        n_source_pages_unassessed: 15, source_pages_unassessed: [45, 46, 47] }] } as unknown as ReportOut;
    const [pages, value] = coverageItems(report);
    expect(pages.sources?.[0].label).toBe("Pages 45-47");
    expect(value.detail).toMatch(/reference list/);
    expect(value.detail).not.toMatch(/embeddable|no eligible text/i);
    expect(value.subIssues?.join(" ")).toContain("Pages 45-47 could not be read reliably");
  });
  it("keeps check-domain order stable without mutating recorded arrays or dropping duplicate sources", () => {
    const native = { paragraph: 7, kind: "paragraph" as const, n_occurrences: 1 };
    const statuses = (["originality_reuse", "statistical_forensics", "citation_integrity", "internal_agreement"] as const).map(check_kind => ({
      ...status, check_kind, assessment_scope: null, analysis_coverage: null,
      coverage_reason: "source_reading_order_unresolved", n_native_equations_unassessed: 2,
      native_math_inventory_state: "complete", native_math_inventory_scope: "main_document_body",
      native_math_coverage_items: [native, { ...native }],
    })) as IntegrityCheckStatusOut[];
    const report = { manuscript_id: 2, check_run_id: 3, integrity_check_status: statuses } as ReportOut;
    const before = JSON.stringify(report);
    const items = coverageItems(report);
    expect(items.map(value => value.key.split(":")[0])).toEqual(["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"]);
    expect(items.find(value => value.key.startsWith("statistical_forensics"))?.sources).toHaveLength(2);
    expect(new Set(items.map(value => value.key)).size).toBe(items.length);
    expect(JSON.stringify(report)).toBe(before);
  });
  it.each(["ai_not_configured", "ai_key_rejected"])(
    "retains the original source limit alongside %s without duplicating source counts",
    (cause) => {
      const report = {
        manuscript_id: 2, check_run_id: 3,
        integrity_check_status: [status, {
          check_kind: "citation_integrity", outcome: "unverifiable",
          n_skipped_quota: 0, n_skipped_api_down: 0, n_skipped_parse_failure: 0,
          coverage_reason: cause,
        }],
      } as ReportOut;
      const before = JSON.stringify(report);
      const rows = coverageItems(report);
      expect(rows).toHaveLength(2);
      expect(rows[0].detail).toContain("Citation matching across the manuscript was not assessed");
      expect(rows[0].subIssues?.join(" ")).toContain("3 reference checks attempted");
      expect(rows[1].detail).toContain("AI-dependent parts of this check were not assessed");
      expect(rows[1].detail).toContain("not a finding against the manuscript");
      expect(rows[1].subIssues).toEqual([]);
      expect(rows[1].key).not.toBe(rows[0].key);
      expect(JSON.stringify(report)).toBe(before);
    },
  );
  it("adds a current limitation without changing a historical report record", () => {
    const report = { manuscript_id: 2, check_run_id: 3, integrity_check_status: [], historical_citation_title_unverified: true } as unknown as ReportOut;
    const before = JSON.stringify(report);
    const notices = coverageItems(report);
    expect(notices[0].label).toBe("Historical title comparison needs review");
    expect(notices[0].detail).toContain("this report has not been recomputed");
    expect(JSON.stringify(report)).toBe(before);
  });
  it("does not confuse metadata checks with full citation or claim assessment", () => {
    const row = item();
    expect(row.label).toContain("Partially assessed");
    expect(row.detail).toContain("Citation matching across the manuscript was not assessed");
    expect(row.subIssues?.join(" ")).toContain("3 reference checks attempted");
    expect(row.subIssues?.join(" ")).toContain("Claim support across the manuscript was not assessed");
    expect(row.subIssues?.join(" ")).not.toContain("24 pages");
    expect(row.action).toBeUndefined();
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

  it("retains source-stop precedence and secondary image locations together", () => {
    const row = item({
      check_kind: "statistical_forensics",
      assessment_scope: null, analysis_coverage: "unsupported",
      coverage_reason: "source_reading_order_unresolved", n_images_unassessed: 1,
      image_coverage_items: [{ image_index: 0, page: 2, paragraph: null, reason: "invalid_response" }],
    });
    expect(row.detail).toContain("This check was not assessed");
    expect(row.detail).not.toContain("Statistical statements were extracted");
    expect(row.subIssues?.join(" ")).toContain("1 image occurrence remains unassessed");
    expect(row.detailsLabel).toBe("source and image assessment details");
    expect(row.sources?.[0].to).toBe("/report/3/document?anchor=page%202&coverage=images");
  });
});


describe("recorded reference attempt quantities", () => {
  it.each([null, undefined])("keeps %s attempt count unknown", count => {
    const row = item({ n_reference_metadata_attempted: count });
    expect(row.subIssues?.join(" ")).toContain("was not recorded");
    expect(row.subIssues?.join(" ")).not.toContain("0 reference checks attempted");
  });
  it("retains a recorded zero separately from unavailable history", () => {
    expect(item({ n_reference_metadata_attempted: 0 }).subIssues?.join(" ")).toContain("0 reference checks attempted");
  });
});


it("distinguishes unconfirmed search identity from unread source metadata", () => {
  const row = item({ assessment_scope: null, coverage_reason: "reference_search_identity_unconfirmed" });
  expect(row.detail).toContain("could not be confirmed as the cited sources");
  expect(row.detail).not.toContain("could not be read");
});
