import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { ResultsTable } from "./ResultsTable";

it("preserves the instructor's resolution without inventing why an earlier suggestion required review", () => {
  const result: ResultRowCommon = {
    criterion_id: 1, text: "Custom criterion", type: "semantic", kind: "semantic", weight: 1,
    weight_importance: "med", outcome: "failed", score: 0, basis: "llm", anchor: null,
    reasoning: null, reason: null, evidence: [],
    resolution: { type: "mark_fail", reason: "The instructor checked the actual requirement.", ai_majority_verdict: "pass" },
  };
  render(<ResultsTable results={[result]} totalCount={1} />);
  expect(screen.getAllByText(/The instructor checked the actual requirement/)).toHaveLength(2);
  expect(screen.getAllByText("Recorded AI suggestion: pass.")).toHaveLength(2);
  expect(screen.queryByText(/agreement threshold|auto-decide|vote leaned/)).not.toBeInTheDocument();
});
