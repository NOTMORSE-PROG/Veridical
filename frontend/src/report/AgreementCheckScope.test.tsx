import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { AgreementScopeOut } from "../api/types";
import { AgreementCheckScope, AgreementScopeSummary } from "./AgreementCheckScope";

const set_aside = Array.from({ length: 10 }, (_, i) => ({
  text: `Another study found result ${i + 1}.`, page_anchor: i === 9 ? null : `p. ${20 + i}`, role: "literature",
}));
const scope = (patch: Partial<AgreementScopeOut> = {}): AgreementScopeOut => ({
  n_objectives: 17, n_results: 34, results_section_identified: true, n_results_set_aside: 10, n_results_too_short: 2,
  set_aside_by_role: [{ role: "literature", count: 10 }], set_aside, absence_leads_withheld: true, ...patch,
});

function show(value: AgreementScopeOut, props: Partial<Parameters<typeof AgreementCheckScope>[0]> = {}, url = "/report/7") {
  return render(<MemoryRouter initialEntries={[url]}><AgreementCheckScope checkRunId={7} scope={value} findings={0} undecided={0}
    compared={31} interrupted={false} limitsLink {...props} /></MemoryRouter>);
}

it("says what the check covered and that it found no possible inconsistency, with no agreeing count", () => {
  show(scope());
  const card = screen.getByRole("region", { name: "What this check compared" });
  expect(within(card).getByText("Found 17 possible objectives and 34 possible results.")).toBeInTheDocument();
  expect(within(card).getByText(/10 results from the literature review were set aside/)).toBeInTheDocument();
  expect(within(card).getByText("The comparison found no possible inconsistency among the pairs it compared.")).toBeInTheDocument();
  expect(within(card).getByText(/so these comparisons do not change readiness/)).toBeInTheDocument();
  expect(within(card).getByText("An objective with no matching result is not reported here.")).toBeInTheDocument();
  expect(card.textContent).not.toMatch(/\b31\b|consistent pair|agreed/);
  expect(within(card).getByRole("link", { name: "Open assessment limits" })).toHaveAttribute("href", "/report/7?section=limits");
});

it("lists the results set aside behind a disclosure, eight at a time, with unique location links", () => {
  show(scope());
  const toggle = screen.getByRole("button", { name: "Show the 10 results set aside" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: "Hide the 10 results set aside" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("heading", { name: "Results set aside" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Result 1 of 10, literature review" })).toBeInTheDocument();
  expect(screen.getByText("Showing 8 of 10 results set aside.")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: "Open location, p. 20, set-aside result 1 of 10" });
  expect(link.getAttribute("href")).toContain("/report/7/document?anchor=p.%2020");
  fireEvent.click(screen.getByRole("button", { name: "Show 2 more" }));
  expect(screen.getByRole("heading", { name: "Result 9 of 10, literature review" })).toHaveFocus();
  expect(screen.getByText("No manuscript location was recorded for this statement.")).toBeInTheDocument();
});

it("keeps the list open across a return, from the URL", () => {
  show(scope(), {}, "/report/7?set_aside=open");
  expect(screen.getByRole("heading", { name: "Results set aside" })).toBeInTheDocument();
});

it("leaves out the outcome while the findings are unknown, and says when the run was interrupted", () => {
  show(scope(), { findings: null, interrupted: true });
  expect(screen.queryByText(/possible inconsistency/)).not.toBeInTheDocument();
  expect(screen.getByText(/The check was interrupted before it compared every pair/)).toBeInTheDocument();
});

it("replaces the set-aside lines when no results section was found, and notes an older run", () => {
  const { unmount } = show(scope({ results_section_identified: false, set_aside: [], set_aside_by_role: [] }));
  expect(screen.getByText(/The results section could not be found/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /results set aside/ })).not.toBeInTheDocument();
  unmount();
  show(scope({ results_section_identified: null, set_aside: [], set_aside_by_role: [], n_results_set_aside: null }));
  expect(screen.getByText("This report was made before VERIDICAL recorded which results it set aside.")).toBeInTheDocument();
});

it("gives the adviser the counts without the statements or list controls", () => {
  render(<AgreementScopeSummary scope={scope({ set_aside: [] })} findings={0} undecided={2} compared={9} />);
  expect(screen.getByRole("heading", { level: 4, name: "What this check compared" })).toBeInTheDocument();
  expect(screen.getByText(/The instructor's report lists the results set aside/)).toBeInTheDocument();
  expect(screen.getByText("The comparison found no possible inconsistency among the pairs it could decide.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
