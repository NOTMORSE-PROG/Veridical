import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { PublicCoverageOut } from "../api/types";
import { SharedLeftOut } from "./SharedLeftOut";

function coverage(changes: Partial<PublicCoverageOut> = {}): PublicCoverageOut {
  return {
    check_kind: "statistical_forensics", coverage_reason: null, n_pages_not_read: 0, pages_not_read: [],
    pages_not_compared: [], not_compared_from: null, n_skipped_service: 0, n_skipped_capacity: 0,
    n_skipped_unreadable: 0, n_references_unverified: 0, n_citation_links_unchecked: 0, n_claims_unchecked: 0,
    n_images_unchecked: 0, n_tables_unchecked: 0, n_equations_unchecked: 0, ...changes,
  };
}

const MANY = [2, 4, 19, 25, 32, 62, 88, 110, 114, 179, 180, 181];

it("lists a long page list behind a button and shows only the counts that are not zero", () => {
  const item = coverage({ n_pages_not_read: MANY.length, pages_not_read: MANY, n_images_unchecked: 81 });
  render(<SharedLeftOut coverage={item} all={[item]} originalityNotCompared={false} />);
  expect(screen.getByRole("heading", { name: "Not covered by this check" })).toBeInTheDocument();
  expect(screen.getByText("12 pages")).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Show page numbers" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: "Hide page numbers" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("179-181")).toBeVisible();
  expect(screen.getByText("Images not checked")).toBeInTheDocument();
  expect(screen.getByText("81")).toBeInTheDocument();
  expect(screen.queryByText("Tables not checked")).not.toBeInTheDocument();
  expect(screen.getByText("Nothing listed here was checked. It is not treated as passed, and it is not a finding.")).toBeInTheDocument();
});

it("shows a short page list inline, says when every check left out the same pages, and gives the reason", () => {
  const item = coverage({ check_kind: "citation_integrity", coverage_reason: "reference_list_unavailable", n_pages_not_read: 3, pages_not_read: [5, 6, 9] });
  const other = coverage({ check_kind: "internal_agreement", n_pages_not_read: 3, pages_not_read: [5, 6, 9] });
  render(<SharedLeftOut coverage={item} all={[item, other]} originalityNotCompared={false} />);
  expect(screen.queryByRole("button", { name: /page numbers/ })).not.toBeInTheDocument();
  expect(screen.getByText("5-6")).toBeInTheDocument();
  expect(screen.getByText("The same pages were left out by every check.")).toBeInTheDocument();
  expect(screen.getByText("Why")).toBeInTheDocument();
  expect(screen.getByText(/The reference list could not be separated reliably/)).toBeInTheDocument();
});

it("does not give a reason that contradicts an originality check that compared nothing", () => {
  const item = coverage({ check_kind: "originality_reuse", coverage_reason: "source_reading_order_partial", n_pages_not_read: 2, pages_not_read: [3, 7] });
  render(<SharedLeftOut coverage={item} all={[item]} originalityNotCompared />);
  expect(screen.queryByText("Why")).not.toBeInTheDocument();
  expect(screen.queryByText(/read the rest of the paper/)).not.toBeInTheDocument();
  expect(screen.getByText("None of the text was compared, so these pages did not change this result.")).toBeInTheDocument();
});

it("says where uncompared text starts, and says when a count was saved without its pages", () => {
  const item = coverage({ check_kind: "originality_reuse", not_compared_from: "p. 158", n_pages_not_read: 4 });
  render(<SharedLeftOut coverage={item} all={[item]} originalityNotCompared={false} />);
  expect(screen.getByText("From p. 158 to the end")).toBeInTheDocument();
  expect(screen.getByText("4 pages. This link does not say which.")).toBeInTheDocument();
});

it("says plainly when the link records nothing about what was left out", () => {
  const item = coverage();
  render(<SharedLeftOut coverage={item} all={[item]} originalityNotCompared={false} />);
  expect(screen.getByText("This link does not record which parts this check left out.")).toBeInTheDocument();
});
