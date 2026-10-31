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
  expect(screen.getByText("VERIDICAL could not decide on 3 of 20 objective and result pairs it compared.")).toBeInTheDocument();
  expect(screen.getByText(/This is not a problem found in the manuscript/)).toBeInTheDocument();
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
  expect(screen.getAllByText("Reason recorded by the check: The texts do not say.")).toHaveLength(2);
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
  fireEvent.click(screen.getByRole("button", { name: "Show the pair" }));
  expect(screen.getByRole("link", { name: "Review this pair, pair 1 of 1" })).toHaveAttribute("href", "/flags/41");
});

it("shows nothing when there is nothing undecided", () => {
  const { container } = show(pairs(0));
  expect(container).toBeEmptyDOMElement();
});

it("recognises only agreement rows that could not be decided as legacy undecided rows", () => {
  expect(isLegacyUndecided(legacy(1))).toBe(true);
  expect(isLegacyUndecided({ ...legacy(2), problem_kind: "agreement_contradictory" })).toBe(false);
});
