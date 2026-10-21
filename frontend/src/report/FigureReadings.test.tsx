import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import type { FigureReadingOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { FigureReadingDetails, FigureReadingsSummary } from "./FigureReadings";

const READING: FigureReadingOut = {
  image_index: 4, status: "available", reason: "visual_reading_only", source_anchor: "paragraph 70",
  source_caption: "Figure 1. Original caption", caption_anchor: "paragraph 72", kind: "diagram",
  source_binding: { document_sha256: "original" }, visible_content: "Two labeled boxes.",
  visible_labels: ["A", "B"], limitations: ["Does not prove working software."],
  support_reason: "The labels are legible.", schema_version: "v1",
};

describe("saved non-scoring figure readings", () => {
  it("keeps observation, original caption and numeric limitations separate", () => {
    renderWithProviders(<FigureReadingsSummary readings={[READING]} checkRunId={12} />);
    expect(screen.getByText(/1 of 1 image occurrences/)).toBeInTheDocument();
    expect(screen.getByText(/do not establish that statistical values/)).toBeInTheDocument();
    expect(screen.getByRole("link", { hidden: true, name: /Open Extracted paragraph 71/ }))
      .toHaveAttribute("href", "/report/12/document?anchor=paragraph%2070&coverage=images");
    expect(screen.getByText(/Nearby source caption/)).toBeInTheDocument();
  });
  it("does not render candidate content for rejected saved readings", () => {
    renderWithProviders(<FigureReadingDetails reading={{ ...READING, status: "needs_review", reason: "support_unconfirmed" }} />);
    expect(screen.queryByText("Two labeled boxes.")).not.toBeInTheDocument();
    expect(screen.getByText(/contents remain unassessed/)).toBeInTheDocument();
  });
  it("reports historical missing readings as not assessed", () => {
    renderWithProviders(<FigureReadingDetails />);
    expect(screen.getByText(/not recorded for this check/)).toBeInTheDocument();
  });
});
