import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { FlagOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { AgreementComparison } from "./AgreementComparison";

const flag = { id: 3, check_run_id: 4, check_kind: "internal_agreement", evidence_excerpt: "An objective candidate", page_anchor: "page 1", ai_verdict_summary: "agreement_contradictory", affects_readiness: false } as FlagOut;

describe("saved agreement evidence", () => {
  it("distinguishes an unmatched objective from a missing historical pair", () => {
    renderWithProviders(<AgreementComparison flag={{ ...flag, ai_verdict_summary: "agreement_unmatched_intent" }} />);
    expect(screen.getByText("One recorded statement; no paired result")).toBeInTheDocument();
    expect(screen.getByText(/does not establish that the manuscript contains no result/)).toBeInTheDocument();
    expect(screen.queryByText("Statement selected as a result")).not.toBeInTheDocument();
  });

  it("preserves historical evidence but never reconstructs a pair from reasoning", () => {
    renderWithProviders(<AgreementComparison flag={{ ...flag, ai_reasoning: 'Result: "invented pair"' }} />);
    expect(screen.getByText("Statement comparison unavailable")).toBeInTheDocument();
    expect(screen.getByText(/An objective candidate/)).toBeInTheDocument();
    expect(screen.queryByText(/invented pair/)).not.toBeInTheDocument();
  });

  it("retains a missing-location statement and encodes only the exact available anchor", () => {
    renderWithProviders(<AgreementComparison flag={{ ...flag, agreement_pair: { intent_text: "Objective <source>", intent_anchor: "page 1 & appendix", outcome_text: "Result source", outcome_anchor: null } }} />);
    expect(screen.getByRole("link", { name: "Open objective location: page 1 & appendix" })).toHaveAttribute("href", "/report/4/document?finding=3&anchor=page%201%20%26%20appendix");
    expect(screen.getByText("No manuscript location was recorded for this statement.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Open result/ })).not.toBeInTheDocument();
    expect(document.querySelectorAll("mark")).toHaveLength(0);
  });
});
