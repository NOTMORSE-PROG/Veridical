import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { FlagSummaryOut, UndeterminedPairsOut } from "../api/types";
import { isLegacyUndecided } from "../domain/undecidedPairs";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import type { EvidenceManifest, EvidenceReference } from "./evidenceTypes";
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
  // That run saved no compared count, so none is invented from the list.
  expect(screen.getByText("VERIDICAL could not decide on 1 objective and result pair.")).toBeInTheDocument();
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


afterEach(() => vi.unstubAllGlobals());

const SNAPSHOT = "e".repeat(64);

function reference(role: EvidenceReference["role"], index: number, quote: string, anchor: string, id: string): EvidenceReference {
  return { reference_id: id.repeat(64), criterion_id: null, flag_id: null, role, index, quote, anchor, source_scope: "own_manuscript", source_state: "not_checked" };
}

function manifestFor(saved: UndeterminedPairsOut): EvidenceManifest {
  return { snapshot_id: SNAPSHOT, assessment_revision: 1, llm_mode: "fake", references: saved.pairs.flatMap((pair, index) => [
    reference("undecided_intent", index, pair.intent_text, pair.intent_anchor, String.fromCharCode(97 + 2 * index)),
    reference("undecided_outcome", index, pair.outcome_text, pair.outcome_anchor, String.fromCharCode(98 + 2 * index)),
  ]) };
}

function showShared(saved: UndeterminedPairsOut, manifest?: EvidenceManifest, failed = false) {
  return renderWithProviders(<UndeterminedPairs shared checkRunId={7} undetermined={saved} legacy={[]} evidence={{ token: "tok", manifest, failed }} />);
}

it("opens each statement's original page from the adviser's card, with what it was compared with", async () => {
  const saved = pairs(1);
  const fetchMock = stubFetchByPath({ [`/shared/tok/evidence/${"a".repeat(64)}/page`]: {
    snapshot_id: SNAPSHOT, image_data_url: null, image_sha256: null, caption: "Rendered from the submitted PDF, page 3.",
    placement: { reference_id: "a".repeat(64), state: "page_only", page: 3, boxes: [], reason: null },
  } });
  vi.stubGlobal("fetch", fetchMock);
  showShared(saved, manifestFor(saved));
  fireEvent.click(screen.getByRole("button", { name: "Show the objective" }));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Open page, result 1 of 1 for objective 1, p. 40" })).toBeInTheDocument();
  const open = screen.getByRole("button", { name: "Open page, objective 1, p. 3" });
  fireEvent.click(open);
  const dialog = await screen.findByRole("dialog", { name: "Objective 1, p. 3" });
  expect(within(dialog).getByText("Statement selected as an objective")).toBeInTheDocument();
  expect(within(dialog).getByText("“Result 1”")).toBeInTheDocument();
  expect(within(dialog).getByText(/This is not a finding and does not change readiness/)).toBeInTheDocument();
  expect(await within(dialog).findByText("Rendered from the submitted PDF, page 3.")).toBeInTheDocument();
});

it("keeps the cards as text when the original pages could not be loaded", () => {
  showShared(pairs(1), undefined, true);
  fireEvent.click(screen.getByRole("button", { name: "Show the objective" }));
  expect(screen.getByText("Original pages could not be loaded. The quotations above are what VERIDICAL recorded.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Open page/ })).not.toBeInTheDocument();
  expect(screen.getByText("“Objective 1”")).toBeInTheDocument();
});

it("lists the same result once per page, not once per wording, and says when no page was saved", () => {
  const saved: UndeterminedPairsOut = { count: 3, compared_count: 3, pairs: [
    { intent_text: "Objective", intent_anchor: "p. 2", outcome_text: "Same result", outcome_anchor: "p. 40", reason: null },
    { intent_text: "Objective", intent_anchor: "p. 2", outcome_text: "Same result", outcome_anchor: "p. 41", reason: null },
    { intent_text: "Objective", intent_anchor: "p. 2", outcome_text: "Unplaced result", outcome_anchor: "", reason: null },
  ] };
  showShared(saved, manifestFor(saved));
  fireEvent.click(screen.getByRole("button", { name: "Show the objective" }));
  expect(screen.getByText("Compared with 3 results")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open page, result 2 of 3 for objective 1, p. 41" })).toBeInTheDocument();
  expect(screen.getByText("No page was saved for this statement.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /result 3 of 3/ })).not.toBeInTheDocument();
});
