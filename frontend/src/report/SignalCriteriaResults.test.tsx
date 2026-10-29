import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CriterionResultOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { SignalCriteriaResults } from "./SignalReviewSections";

function row(overrides: Partial<CriterionResultOut> = {}): CriterionResultOut {
  return { criterion_id: 1, text: "The source criterion", type: "semantic", kind: "semantic",
    weight: 1, weight_importance: "med", outcome: "passed", score: 100, basis: null,
    anchor: null, reasoning: "Original recorded reasoning.", reason: null, evidence: [], resolution: null,
    source_coverage: { native_math_coverage_version: "v1", native_math_inventory_state: "not_applicable",
      native_math_inventory_scope: null, n_native_equations_unassessed: 0, n_native_equations_unlocated: null,
      native_math_excluded_paragraphs: [], native_math_coverage_items: [] },
    ...overrides };
}

describe("Signal criterion judgment presentation", () => {
  it("does not substitute a binary or AI grade for a missing recorded instructor level", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[row({
      level: null, resolution: { type: "mark_level", reason: "", ai_majority_verdict: "Exemplary" },
    })]} />);
    expect(screen.getAllByText("Recorded grade unavailable")).toHaveLength(2);
    expect(screen.getByText("No comment recorded.")).toBeVisible();
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
    fireEvent.click(screen.getByText("Recorded assessment details"));
    const actions = screen.getAllByRole("link");
    expect(actions).toHaveLength(2);
    expect(actions[0]).toHaveAccessibleName("Open in manuscript: page 1. Criterion 1: The source criterion");
    expect(actions[0]).toHaveAttribute("href", "/report/3/document?anchor=page%201");
    expect(actions[1]).toHaveAttribute("href", "/report/3/document?anchor=page%202");
    expect(screen.getByText("“Another quote on the same page”")).toBeInTheDocument();
    expect(screen.getByText("Recorded assessment details")).toHaveAttribute("id", "signal-criterion-evidence-3-1");
  });

  it("retains a distinct row fallback, omits blank actions, and encodes original anchors", () => {
    const rawAnchor = " appendix & <figure> ";
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[
      row({ anchor: "paragraph 3", evidence: [
        { quote: "Unplaced excerpt", anchor: "  " },
        { quote: "Unsupported stored anchor", anchor: rawAnchor },
      ] }),
    ]} />);
    fireEvent.click(screen.getByText("Recorded assessment details"));
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
    for (const summary of screen.getAllByText("Recorded assessment details")) fireEvent.click(summary);
    expect(screen.queryAllByRole("link")).toHaveLength(publicView ? 0 : 2);
    expect(screen.queryByText("Recorded criterion location: page 1")).not.toBeInTheDocument();
    expect(screen.getByText("“Recorded quote”")).toBeInTheDocument();
  });
});


describe("retained assessment provenance", () => {
  it.each([false, true])("distinguishes retained AI content from the current instructor judgment (public=%s)", (publicView) => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView={publicView} results={[row({ basis: "llm", resolved: publicView,
      outcome: "not_applicable", score: null, evidence: [{ quote: "Original automated excerpt", anchor: "paragraph 2" }],
      resolution: { type: "needs_document", reason: "Private reason for excluding this criterion.", ai_majority_verdict: null },
    })]} />);
    fireEvent.click(screen.getByText("Earlier AI assessment"));
    expect(screen.getByText("Earlier AI explanation:")).toBeVisible();
    expect(screen.getByText("Original recorded reasoning.")).toBeVisible();
    expect(screen.getByText("Recorded manuscript excerpts")).toBeVisible();
    expect(screen.getAllByText("A recorded location does not establish support for a grade.")).toHaveLength(1);
    if (publicView) {
      expect(screen.queryByText("Private reason for excluding this criterion.")).not.toBeInTheDocument();
      expect(screen.queryByText(/reason is shown above/)).not.toBeInTheDocument();
      expect(screen.getByText(/They do not record the instructor's reason/)).toBeVisible();
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    } else {
      expect(screen.getByText("Excluded from readiness calculation: needs another document. No grade recorded.")).toBeVisible();
      expect(screen.getByText(/The instructor's reason is shown above/)).toBeVisible();
      expect(screen.getByRole("link")).toHaveAttribute("href", "/report/3/document?anchor=paragraph%202");
    }
  });

  it("keeps the rule writer's null basis distinct from unrecognized basis strings", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[
      row({ type: "structural", kind: "structural" }),
      row({ criterion_id: 2, text: "Unsupported source", type: "structural", kind: "structural", basis: "future-unknown" }),
    ]} />);
    fireEvent.click(screen.getByText("Rule-check details")); fireEvent.click(screen.getByText("Recorded assessment details"));
    expect(screen.getByText("Recorded rule-check explanation:")).toBeVisible();
    expect(screen.getByText("Recorded explanation, source not identified:")).toBeVisible();
    expect(screen.queryByText(/AI explanation:/)).not.toBeInTheDocument();
  });

  it("does not attribute an inconsistent not-assessable tuple to AI, including its unresolved branch", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView results={[row({ type: "not_assessable", basis: "llm", outcome: "escalated", score: null })]} />);
    fireEvent.click(screen.getByText("Recorded assessment details"));
    expect(screen.getByText("Recorded explanation, source not identified:")).toBeVisible();
    expect(screen.queryByText(/AI explanation:/)).not.toBeInTheDocument();
    expect(screen.queryByText("No grade accepted")).not.toBeInTheDocument();
  });

  it("keeps essential unresolved AI context but uses one empty assessment state", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView results={[row({ basis: "llm", outcome: "escalated", score: null, reasoning: null })]} />);
    fireEvent.click(screen.getByText("AI assessment details"));
    expect(screen.getByText("No grade accepted")).toBeVisible();
    expect(screen.getAllByText("No assessment details are available in this record.")).toHaveLength(1);
    expect(screen.queryByText(/No AI explanation is available/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No excerpt is available/)).not.toBeInTheDocument();
  });
});

it("keeps a criterion still waiting in its place, linked to where it is graded", () => {
  renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[row(), row({ criterion_id: 2, text: "Bibliography", outcome: "escalated", score: null })]} />);
  expect(screen.getByText("2 records")).toBeInTheDocument();
  expect(screen.getByText("Waiting for your judgment")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Grade this criterion: Bibliography" }))
    .toHaveAttribute("href", "/report/3/document?view=decision&step=criteria&pane=review&criterion=2");
});

it("does not present a standalone recorded location as an excerpt", () => {
  renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[row({ anchor: "paragraph 2", reasoning: null })]} />);
  fireEvent.click(screen.getByText("Recorded assessment details"));
  expect(screen.queryByRole("heading", { name: "Recorded manuscript excerpts" })).not.toBeInTheDocument();
  expect(screen.getByText("Recorded criterion location: paragraph 2")).toBeVisible();
  expect(screen.getByText("No excerpt is available in this assessment record.")).toBeVisible();
  expect(screen.getByRole("link")).toHaveAttribute("href", "/report/3/document?anchor=paragraph%202");
});
