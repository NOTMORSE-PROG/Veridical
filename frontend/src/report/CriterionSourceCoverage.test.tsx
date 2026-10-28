import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { CriterionResultOut, CriterionSourceCoverage as Coverage } from "../api/types";
import { criterionAssessmentDetails } from "../domain/criterionAssessment";
import { privateCriterionCoverage } from "../domain/criterionSourceCoverage";
import { CriterionSourceCoverage } from "./CriterionSourceCoverage";

const zero: Coverage = { native_math_coverage_version: "v1", native_math_inventory_state: "complete",
  native_math_inventory_scope: "main_document_body", n_native_equations_unassessed: 0,
  n_native_equations_unlocated: 0, native_math_excluded_paragraphs: [], native_math_coverage_items: [] };
const row: CriterionResultOut = { criterion_id: 1, text: "The custom requirement", type: "semantic", kind: "semantic", basis: "llm",
  weight: 1, weight_importance: "med", outcome: "passed", score: 100, reasoning: null,
  reason: null, anchor: null, evidence: [], resolution: null };
function show(value: Coverage) {
  return render(<CriterionSourceCoverage value={value} criterionText={row.text} firstSlot={4}
    sourceAction={(anchor, slot) => <a id={`source-${slot}`} href={`#${anchor}`}>Open {anchor}</a>} />);
}
it.each(["not_recorded", "unavailable"] as const)("does not invent a Word source for %s coverage", state => {
  show({ ...zero, native_math_coverage_version: null, native_math_inventory_state: state,
    native_math_inventory_scope: null, n_native_equations_unassessed: null,
    n_native_equations_unlocated: null });
  expect(screen.getByText(/It is unknown whether the manuscript contains equations or how many/)).toBeVisible();
  expect(screen.queryByText(/Word/)).not.toBeInTheDocument();
});
it("keeps a measured zero quiet and distinct from non-applicable coverage", () => {
  show(zero);
  expect(screen.getByText("The saved main-document-body inventory recorded no native Word equations.")).toBeVisible();
  expect(screen.queryByText(/Source content omitted/)).not.toBeInTheDocument();
  const notApplicable: Coverage = { ...zero, native_math_inventory_state: "not_applicable",
    native_math_inventory_scope: null, n_native_equations_unlocated: null };
  const privateRow: CriterionResultOut = { ...row, source_coverage: notApplicable };
  expect(privateCriterionCoverage(privateRow, false)).toBeNull();
});
it("keeps a consequential unlocated input limit visible without opening nested locations", () => {
  const value: Coverage = { ...zero, n_native_equations_unassessed: 1, n_native_equations_unlocated: 1,
    native_math_coverage_items: [{ paragraph: null, kind: "paragraph", n_occurrences: 1 }] };
  show(value);
  expect(screen.getByText(/does not establish that the remaining text and tables could be used safely/)).toBeVisible();
  expect(screen.getByText("Source scope and locations").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByRole("link", { hidden: true })).not.toBeInTheDocument();
  expect(screen.queryByText(/Statistical forensics|criterion.*unassessed/i)).not.toBeInTheDocument();
});
it("groups physical multiplicity for display and gives distinct locations unique action slots", () => {
  show({ ...zero, n_native_equations_unassessed: 3, native_math_excluded_paragraphs: [7, 9],
    native_math_coverage_items: [{ paragraph: 7, kind: "table_cell", n_occurrences: 1 },
      { paragraph: 7, kind: "table_cell", n_occurrences: 1 }, { paragraph: 9, kind: "paragraph", n_occurrences: 1 }] });
  expect(screen.getByText("Extracted paragraph 8, table: 2 equation occurrences omitted.")).toBeInTheDocument();
  const links = screen.getAllByRole("link", { hidden: true });
  expect(links.map(link => link.id)).toEqual(["source-4", "source-5"]);
});
it.each([undefined, { ...zero, n_native_equations_unassessed: true }, { ...zero, n_native_equations_unassessed: "0" },
  { ...zero, n_native_equations_unassessed: 2 }, { ...zero, native_math_coverage_version: "v2" }])(
  "does not turn absent or malformed private coverage into measured zero: %s", value => {
    const details = criterionAssessmentDetails({ ...row, source_coverage: value } as unknown as CriterionResultOut, false);
    expect(details.hasDetails).toBe(true);
    expect(details.summary).toMatch(/source coverage (not recorded|unavailable)/);
    expect(details.sourceCoverage?.n_native_equations_unassessed).toBeNull();
  });
it("keeps public omission out of summary, detail availability and fallback even with injected private data", () => {
  for (const value of [undefined, zero, { ...zero, native_math_inventory_state: "unavailable" }]) {
    const details = criterionAssessmentDetails({ ...row, source_coverage: value } as CriterionResultOut, true);
    expect(details.hasDetails).toBe(false); expect(details.sourceCoverage).toBeNull();
    expect(details.summary).toBe("AI assessment details");
  }
});
it("does not invent a Word warning on an unrelated rule's missing coverage", () => {
  const details = criterionAssessmentDetails({ ...row, type: "structural", kind: "structural", basis: null }, false);
  expect(details.hasDetails).toBe(false); expect(details.sourceCoverage).toBeNull();
  expect(details.summary).toBe("Rule-check details");
});
