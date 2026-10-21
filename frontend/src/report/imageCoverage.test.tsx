import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import labels from "../../../backend/app/report/data/image_coverage_labels.json";
import type { IntegrityCheckStatusOut } from "../api/types";
import { CoverageStatement } from "../ui/CoverageStatement";
import { imageCoverageItem, IMAGE_COVERAGE_LABELS } from "./imageCoverage";

const status: IntegrityCheckStatusOut = {
  check_kind: "statistical_forensics", outcome: "escalated",
  n_skipped_quota: 0, n_skipped_api_down: 0, n_skipped_parse_failure: 0,
  coverage_reason: "image_content_unassessed", assessment_scope: "available_text_and_tables",
  analysis_coverage: "partial", n_images_unassessed: 2,
  image_coverage_items: [
    { image_index: 0, page: null, paragraph: 4, reason: "unsupported_source" },
    { image_index: 1, page: null, paragraph: 4, reason: "unsupported_source" },
  ],
};

describe("saved image assessment coverage", () => {
  it("keeps screen and exported reason labels identical", () => {
    expect(IMAGE_COVERAGE_LABELS).toEqual(labels);
  });

  it("groups repeated occurrences without inventing semantic figure counts or pages", () => {
    const item = imageCoverageItem(status, 12);
    expect(item.label).toContain("Partially assessed");
    expect(item.detail).toContain("Statistical statements were extracted");
    expect(item.detail).not.toContain("statistics checked");
    expect(item.sources).toHaveLength(1);
    expect(item.sources![0].label).toContain("Extracted paragraph 5");
    expect(item.sources![0].label).toContain("2 image occurrences");
    expect(item.sources![0].to).toBe("/report/12/document?anchor=paragraph%204&coverage=images");
    expect(item.action?.label).toBe("Open the paper");
  });

  it("does not call an unread-image manuscript qualitative or passed", () => {
    const item = imageCoverageItem({ ...status, analysis_coverage: "unsupported" }, 12);
    expect(item.label).toContain("Not assessed");
    expect(item.detail).toContain("Unassessed images may contain information this check missed");
    expect(item.detail).not.toContain("Applicable checks ran");
  });

  it("does not create a link for a missing or invalid source location", () => {
    const item = imageCoverageItem({
      ...status, image_coverage_items: [{ image_index: 0, page: -1, paragraph: null, reason: "invalid_response" }],
    }, 12);
    expect(item.sources![0].to).toBeNull();
    expect(item.sources![0].label).toContain("Source location unavailable");
  });

  it("keeps the main limitation visible while details are collapsed", () => {
    const item = imageCoverageItem(status, 12);
    render(<MemoryRouter><CoverageStatement state="partial" headingId="coverage"
      heading="Assessment limits" intro="Saved assessment coverage" items={[item]} /></MemoryRouter>);
    expect(screen.getByText(/Some image content remains unassessed/)).toBeVisible();
    const toggle = screen.getByRole("button", { name: "Show image assessment details" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: /Extracted paragraph 5/ })).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)).toBeVisible();
    expect(screen.getByRole("link", { name: /Extracted paragraph 5/ })).toBeVisible();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Run again" })).not.toBeInTheDocument();
  });
});
