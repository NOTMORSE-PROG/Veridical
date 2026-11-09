import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { FlagSummaryOut, UndeterminedPairsOut } from "../api/types";
import { isLegacyUndecided } from "../domain/undecidedPairs";
import { UndeterminedPairs } from "./UndeterminedPairs";

function pairs(count: number): UndeterminedPairsOut {
  return {
    count,
    compared_count: 20,
    pairs: Array.from({ length: count }, (_, i) => ({
      intent_text: `Objective ${i + 1}`, intent_anchor: `p. ${i + 3}`,
      outcome_text: `Result ${i + 1}`, outcome_anchor: `p. ${i + 40}`, reason: "The texts do not say.",
    })),
  };
}

function legacy(id: number): FlagSummaryOut {
  return {
    id, check_kind: "internal_agreement", severity: "low", criterion_text: null, evidence_excerpt: `Old objective ${id}`,
    page_anchor: "p. 2", overridden: false, is_passage_level: false, first_upload_context: false,
    confirmed_citation_source: false, problem_kind: "agreement_cannot_determine",
  };
}

function show(undetermined: UndeterminedPairsOut | null, old: FlagSummaryOut[] = []) {
  return render(<MemoryRouter><UndeterminedPairs checkRunId={7} undetermined={undetermined} legacy={old} /></MemoryRouter>);
}

it("states the undecided pairs once, collapsed, without calling them a problem", () => {
  show(pairs(3));
  expect(screen.getByText("3 selected pairs left unresolved")).toBeInTheDocument();
  expect(screen.getByText("Pairs compared with AI: 20.")).toBeInTheDocument();
  expect(screen.getByText(/These pairs are not manuscript findings and do not change readiness/)).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Show 3 pairs" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Pairs to read yourself")).not.toBeInTheDocument();
});

it("opens to a numbered list with unique location links and the recorded reason", () => {
  show(pairs(2));
  fireEvent.click(screen.getByRole("button", { name: "Show 2 pairs" }));
  expect(screen.getByRole("button", { name: "Hide 2 pairs" })).toHaveAttribute("aria-expanded", "true");
  const list = screen.getByRole("list");
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByRole("link", { name: /Open result location, .*pair 2 of 2/ })).toBeInTheDocument();
  expect(screen.getAllByText("Comparison status not recorded: The texts do not say.")).toHaveLength(2);
});

it("pages a long list and says how many are shown", () => {
  show(pairs(10));
  fireEvent.click(screen.getByRole("button", { name: "Show 10 pairs" }));
  expect(screen.getByRole("status")).toHaveTextContent("Showing 8 of 10 pairs.");
  fireEvent.click(screen.getByRole("button", { name: "Show 2 more" }));
  expect(screen.getByRole("status")).toHaveTextContent("Showing 10 of 10 pairs.");
  expect(screen.getByRole("heading", { name: "Pair 9 of 10" })).toHaveFocus();
});

it("folds saved rows from older runs into the same list, each linking to its record", () => {
  show(null, [legacy(41)]);
  // That run saved no compared count, so none is invented from the list.
  expect(screen.getByText("1 selected pair left unresolved")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the pair" }));
  expect(screen.getByRole("link", { name: "Review this pair, pair 1 of 1" })).toHaveAttribute("href", "/flags/41");
  expect(screen.getByText("The number of pairs compared with AI was not recorded.")).toBeInTheDocument();
});

it("keeps a pair declined before comparison separate from actual AI comparisons", () => {
  show({ ...pairs(1), compared_count: 0, locally_unassessed_count: 1 });
  expect(screen.getByText("Pairs compared with AI: 0.")).toBeInTheDocument();
  expect(screen.getByText("Pairs left unassessed before AI comparison: 1.")).toBeInTheDocument();
  expect(screen.queryByText(/of 1.*it compared/)).not.toBeInTheDocument();
});

it("shows nothing when there is nothing undecided", () => {
  const { container } = show(pairs(0));
  expect(container).toBeEmptyDOMElement();
});

it.each([false, true])("retains independent comparison and local-abstention counts on shared=%s", (shared) => {
  const saved = pairs(2);
  saved.compared_count = 2;
  saved.locally_unassessed_count = 1;
  saved.pairs[0] = { ...saved.pairs[0], comparison_method: "phase_guard", reason: "Different releases." };
  saved.pairs[1] = { ...saved.pairs[1], comparison_method: "generation", reason: "The subject is unclear." };
  render(<MemoryRouter><UndeterminedPairs shared={shared} checkRunId={7} undetermined={saved} legacy={[]} /></MemoryRouter>);
  expect(screen.getByText("Pairs compared with AI: 2.")).toBeInTheDocument();
  expect(screen.getByText("Pairs left unassessed before AI comparison: 1.")).toBeInTheDocument();
  expect(screen.getByText("2 selected pairs left unresolved")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: shared ? "Show the objectives" : "Show 2 pairs" }));
  expect(screen.getByText("Left unassessed before AI comparison: Different releases.")).toBeInTheDocument();
  expect(screen.getByText("AI comparison was inconclusive: The subject is unclear.")).toBeInTheDocument();
  if (shared) {
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getAllByText("Selected result: 1")).toHaveLength(2);
    expect(screen.queryByText(/Compared with/)).not.toBeInTheDocument();
  }
});

it("keeps a zero AI count and a selected local pair in the shared report", () => {
  const saved = { ...pairs(1), compared_count: 0, locally_unassessed_count: 1 };
  saved.pairs[0].comparison_method = "phase_guard";
  render(<MemoryRouter><UndeterminedPairs shared checkRunId={7} undetermined={saved} legacy={[]} /></MemoryRouter>);
  expect(screen.getByText("Pairs compared with AI: 0.")).toBeInTheDocument();
  expect(screen.getByText("Pairs left unassessed before AI comparison: 1.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the objective" }));
  expect(screen.getByText("Selected result: 1")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(screen.queryByText(/close enough|Compared with/)).not.toBeInTheDocument();
});

it("does not invent a local count when that count was not recorded", () => {
  show({ ...pairs(1), compared_count: 2, locally_unassessed_count: null });
  expect(screen.getByText("Pairs compared with AI: 2.")).toBeInTheDocument();
  expect(screen.queryByText(/Pairs left unassessed before AI comparison/)).not.toBeInTheDocument();
});

it("shows recovered legacy statements with unknown comparison status and no private shared links", () => {
  const saved = new Map([[41, { intent_text: "Legacy objective", intent_anchor: "p. 3", outcome_text: "Legacy result", outcome_anchor: "p. 5" }]]);
  render(<MemoryRouter><UndeterminedPairs shared checkRunId={7} undetermined={null} legacy={[legacy(41)]} legacyPairs={saved} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Show the objective" }));
  expect(screen.getByText("Comparison status not recorded.")).toBeInTheDocument();
  expect(screen.getByText("“Legacy result”")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it("recognises only agreement rows that could not be decided as legacy undecided rows", () => {
  expect(isLegacyUndecided(legacy(1))).toBe(true);
  expect(isLegacyUndecided({ ...legacy(2), problem_kind: "agreement_contradictory" })).toBe(false);
});

it("shows the adviser's objective cards four more at a time until every objective is shown", () => {
  render(<MemoryRouter><UndeterminedPairs shared checkRunId={7} undetermined={pairs(9)} legacy={[]} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Show the objectives" }));
  const cards = () => screen.getAllByRole("listitem").filter(item => item.classList.contains("signal-pair-card"));
  expect(cards()).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Show 4 more" }));
  expect(cards()).toHaveLength(8);
  expect(document.activeElement).toBe(cards()[4]);
  fireEvent.click(screen.getByRole("button", { name: "Show 1 more" }));
  expect(cards()).toHaveLength(9);
  expect(screen.queryByRole("button", { name: /more$/ })).not.toBeInTheDocument();
});
