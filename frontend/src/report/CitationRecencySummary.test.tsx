import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CitationRecencySummary } from "./CitationRecencySummary";

describe("CitationRecencySummary", () => {
  it("shows the counts as a note beside the citation check", () => {
    render(<CitationRecencySummary recency={{ status: "assessed", window_years: 5, reference_year: 2023, reference_year_basis: "manuscript_year",
      oldest_year_in_window: 2018, counts_toward_readiness: false, n_references: 20, n_dated: 18, n_outside_window: 2, n_within_window: 16,
      n_dated_after_reference_year: 0, n_year_not_readable: 0 }} />);
    expect(screen.getByText("Citation recency: 2 of 18 dated sources are older than the 5-year window.")).toBeInTheDocument();
  });

  it("shows nothing for a run checked before the note existed", () => {
    const { container } = render(<CitationRecencySummary recency={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
