import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { OriginalityScope as Scope } from "../api/types";
import { OriginalityScope } from "./OriginalityScope";
import { OriginalitySetAside, StandardWordingLeftOut } from "./OriginalitySetAside";

const base: Scope = { compared_count: 12, corpus: "library", not_compared_reason: null };

it("lists the passages not raised by reason, wording first, with the standards named and zeros hidden", () => {
  render(<OriginalitySetAside scope={{ ...base, n_set_aside_cited_quotation: 1, n_set_aside_standard_text: 2,
    n_set_aside_weak_section_evidence: 0, n_set_aside_no_shared_wording: 3,
    standard_texts_set_aside: ["ISO/IEC 25010 usability definition", "Approval sheet statement"] }} />);
  expect(screen.getByRole("heading", { name: "Similar passages not raised" })).toBeInTheDocument();
  expect(screen.getByText("These were set aside. They are not findings and not evidence of originality. Only the counts were saved; the passages are not listed.")).toBeInTheDocument();
  const labels = screen.getAllByRole("term").map(term => term.textContent);
  expect(labels).toEqual(["Cited quotation", "Standard wording", "No shared wording"]);
  expect(screen.getByText(/^passage\. The only shared wording was a marked, cited quotation\./)).toBeInTheDocument();
  expect(screen.getByText("ISO/IEC 25010 usability definition")).toBeInTheDocument();
  expect(screen.getByText("One passage can count under both Cited quotation and Standard wording.")).toBeInTheDocument();
  expect(screen.queryByText("Weak section match")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/%/);
});

it("says when none were set aside, and when an older run did not record it", () => {
  const { unmount } = render(<OriginalitySetAside scope={{ ...base, n_set_aside_cited_quotation: 0, n_set_aside_standard_text: 0,
    n_set_aside_weak_section_evidence: 0, n_set_aside_no_shared_wording: 0 }} />);
  expect(screen.getByText("None were set aside.")).toBeInTheDocument();
  unmount();
  render(<OriginalitySetAside scope={base} />);
  expect(screen.getByText("Not recorded for this run.")).toBeInTheDocument();
});

it("says some reasons were not recorded, and when a standard count has no names", () => {
  render(<OriginalitySetAside scope={{ ...base, n_set_aside_standard_text: 1, n_set_aside_no_shared_wording: 2 }} />);
  expect(screen.getByText(/Not named in this record\./)).toBeInTheDocument();
  expect(screen.getByText("Some reasons were not recorded for this run.")).toBeInTheDocument();
});

it("names the standard wording left out of a raised finding, never its text", () => {
  const { container, rerender } = render(<StandardWordingLeftOut names={["ISO/IEC 25010 usability definition"]} />);
  expect(screen.getByText("Standard wording not counted")).toBeInTheDocument();
  expect(screen.getByText("Shading shows all shared wording, including this standard wording. VERIDICAL did not count the standard wording.")).toBeInTheDocument();
  rerender(<StandardWordingLeftOut names={[]} />);
  expect(container).toBeEmptyDOMElement();
});

it("says how many readable pages the comparison used only when the run recorded it", () => {
  const { rerender } = render(<MemoryRouter><OriginalityScope scope={base} pages={{ compared: 124, withText: 211, unread: 92 }} /></MemoryRouter>);
  expect(screen.getByText("Originality compared 124 of the 211 pages it could read. 92 pages could not be read reliably.")).toBeInTheDocument();
  rerender(<MemoryRouter><OriginalityScope scope={base} pages={{ compared: 211, withText: 211 }} /></MemoryRouter>);
  expect(screen.getByText("Originality compared all 211 pages it could read.")).toBeInTheDocument();
  rerender(<MemoryRouter><OriginalityScope scope={base} /></MemoryRouter>);
  expect(screen.queryByText(/pages it could read/)).not.toBeInTheDocument();
});
