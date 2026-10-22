import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ResultRowCommon } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { SignalCriteriaResults } from "./SignalReviewSections";

function row(overrides: Partial<ResultRowCommon> = {}): ResultRowCommon {
  return { criterion_id: 1, text: "The source criterion", type: "semantic", kind: "semantic",
    weight: 1, weight_importance: "med", outcome: "passed", score: 100, basis: null,
    anchor: null, reasoning: "Original recorded reasoning.", reason: null, evidence: [], resolution: null,
    ...overrides };
}

describe("Signal criterion judgment presentation", () => {
  it("does not substitute a binary or AI grade for a missing recorded instructor level", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[row({
      level: null, resolution: { type: "mark_level", reason: "", ai_majority_verdict: "Exemplary" },
    })]} />);
    expect(screen.getAllByText("Recorded grade unavailable")).toHaveLength(2);
    expect(screen.getByText("No instructor reason is available in this record.")).toBeVisible();
    expect(screen.queryByText("Meets criterion")).not.toBeInTheDocument();
    expect(screen.queryByText("Exemplary")).not.toBeInTheDocument();
  });
  it.each([false, true])("uses each rubric's exact level rather than pass/fail (public=%s)", (publicView) => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView={publicView} results={[
      row({ score: 25, level: { name: "Limited", ordinal: 1, points: 1, max_points: 4 } }),
      row({ criterion_id: 2, score: 50, level: { name: "Satisfactory", ordinal: 2, points: 2, max_points: 4 } }),
      row({ criterion_id: 3, outcome: "failed", score: 0, level: { name: "Custom zero rung", ordinal: 1, points: 0, max_points: 3 } }),
      row({ criterion_id: 4, level: { name: "Custom highest rung", ordinal: 3, points: 3, max_points: 3 } }),
    ]} />);
    for (const name of ["Limited", "Satisfactory", "Custom zero rung", "Custom highest rung"]) {
      expect(screen.getByText(`Level: ${name}`)).toHaveClass("signal-outcome--neutral");
    }
    expect(screen.queryByText("Meets criterion")).not.toBeInTheDocument();
    expect(screen.queryByText("Recorded level:")).not.toBeInTheDocument();
  });

  it("keeps full, partial, failed and unassessed ordinary results distinct", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[
      row(), row({ criterion_id: 2, score: 50 }),
      row({ criterion_id: 3, outcome: "failed", score: 0 }),
      row({ criterion_id: 4, outcome: "unverifiable", score: null }),
    ]} />);
    expect(screen.getByText("Meets criterion")).toHaveClass("signal-outcome--meets");
    expect(screen.getByText("Partly meets criterion")).toHaveClass("signal-outcome--warning");
    expect(screen.getByText("Does not meet")).toHaveClass("signal-outcome--does-not-meet");
    expect(screen.getByText("Unverifiable")).toHaveClass("signal-outcome--neutral");
    expect(screen.queryByText(/50%/)).not.toBeInTheDocument();
  });

  it("links each distinct excerpt location even without a row-level anchor", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[
      row({ evidence: [
        { quote: "First recorded quote", anchor: "page 1" },
        { quote: "Second recorded quote", anchor: "page 2" },
        { quote: "Another quote on the same page", anchor: "page 1" },
      ] }),
    ]} />);
    fireEvent.click(screen.getByText("Review evidence and reasoning"));
    const actions = screen.getAllByRole("link");
    expect(actions).toHaveLength(2);
    expect(actions[0]).toHaveAccessibleName("Open in manuscript: page 1. Criterion 1: The source criterion");
    expect(actions[0]).toHaveAttribute("href", "/report/3/document?anchor=page%201");
    expect(actions[1]).toHaveAttribute("href", "/report/3/document?anchor=page%202");
    expect(screen.getByText("“Another quote on the same page”")).toBeInTheDocument();
    expect(screen.getByText("Review evidence and reasoning")).toHaveAttribute("id", "signal-criterion-evidence-3-1");
  });

  it("retains a distinct row fallback, omits blank actions, and encodes original anchors", () => {
    const rawAnchor = " appendix & <figure> ";
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[
      row({ anchor: "paragraph 3", evidence: [
        { quote: "Unplaced excerpt", anchor: "  " },
        { quote: "Unsupported stored anchor", anchor: rawAnchor },
      ] }),
    ]} />);
    fireEvent.click(screen.getByText("Review evidence and reasoning"));
    expect(screen.getByText("No manuscript location was recorded for this excerpt.")).toBeInTheDocument();
    const actions = screen.getAllByRole("link");
    expect(actions).toHaveLength(2);
    expect(actions[0]).toHaveAttribute("href", `/report/3/document?anchor=${encodeURIComponent(rawAnchor)}`);
    expect(actions[1]).toHaveAttribute("href", "/report/3/document?anchor=paragraph%203");
    expect(screen.getByText("Recorded criterion location: paragraph 3")).toBeInTheDocument();
  });

  it.each([false, true])("deduplicates a matching row anchor and protects public navigation (public=%s)", (publicView) => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView={publicView} results={[
      row({ anchor: "page 1", evidence: [{ quote: "Recorded quote", anchor: "page 1" }] }),
      row({ criterion_id: 2, anchor: "page 2", evidence: [] }),
    ]} />);
    for (const summary of screen.getAllByText("Review evidence and reasoning")) fireEvent.click(summary);
    expect(screen.queryAllByRole("link")).toHaveLength(publicView ? 0 : 2);
    expect(screen.queryByText("Recorded criterion location: page 1")).not.toBeInTheDocument();
    expect(screen.getByText("“Recorded quote”")).toBeInTheDocument();
  });
});
