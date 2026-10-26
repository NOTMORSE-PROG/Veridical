import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { sampleReport } from "../demo/workspaceFixture";
import { ReviewProgress } from "./ReviewProgress";

afterEach(cleanup);
describe("saved review progression", () => {
  it("counts saved outcomes and exposes the final decision only after all judgments", () => {
    const report = structuredClone(sampleReport);
    const view = render(<ReviewProgress report={report} />);
    expect(screen.getByText("0 of 4 criteria recorded")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0");
    report.results[0].outcome = "passed";
    report.pending_review_count = 3;
    view.rerender(<ReviewProgress report={report} />);
    expect(screen.getByText("1 of 4 criteria recorded")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Continue rubric review/ })).toHaveAttribute("href", "#review-criteria");
    report.results.forEach(row => { row.outcome = "passed"; });
    report.pending_review_count = 0;
    view.rerender(<ReviewProgress report={report} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "4");
    expect(screen.getByRole("link", { name: /Continue to final decision/ })).toHaveAttribute("href", "#final-decision");
    report.decision = "returned";
    view.rerender(<ReviewProgress report={report} />);
    expect(screen.getByText("Returned for revision")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
