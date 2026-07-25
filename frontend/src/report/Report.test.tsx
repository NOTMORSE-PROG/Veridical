import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ReportPage } from "./Report";

const READY_REPORT = {
  check_run_id: 1,
  manuscript_group_label: "G-11",
  rubric_title: "TIP Format",
  status: "ready",
  composite_score: 92.5,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: null,
  results: [
    {
      criterion_id: 1,
      text: "Has an Abstract",
      type: "structural",
      weight: 20,
      kind: "structural",
      outcome: "passed",
      score: 100,
      basis: null,
      anchor: "page 2",
      reasoning: null,
      reason: null,
      evidence: [],
    },
    {
      criterion_id: 2,
      text: "Chapter 1 clearly states the problem",
      type: "semantic",
      weight: 40,
      kind: "semantic",
      outcome: "passed",
      score: 50,
      basis: "llm",
      anchor: null,
      reasoning: "Partially addresses the problem statement.",
      reason: null,
      evidence: [{ quote: "The real evidence sentence.", anchor: "page 3" }],
    },
    {
      criterion_id: 3,
      text: "The Glossary defines all key terms",
      type: "semantic",
      weight: 40,
      kind: "semantic",
      outcome: "escalated",
      score: null,
      basis: "llm",
      anchor: null,
      reasoning: null,
      reason: "Could not verify the quoted evidence after a retry.",
      evidence: [],
    },
  ],
};

const ESCALATED_ITEMS = [
  {
    check_result_id: 3,
    criterion_id: 3,
    criterion_text: "The Glossary defines all key terms",
    weight: 40,
    agreement: 0.333,
    votes: ["pass", "fail", "partial"],
    ai_majority_verdict: null,
    reason: "Could not verify the quoted evidence after a retry.",
  },
];

const NEEDS_REVIEW_REPORT = {
  ...READY_REPORT,
  status: "needs_review",
  composite_score: null,
  reason: "No criteria could be auto-scored (all escalated/not-applicable).",
  results: [],
};

describe("ReportPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the status pill and composite score", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/1/report": READY_REPORT,
        "/check-runs/1/escalated": ESCALATED_ITEMS,
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("92.5%")).toBeInTheDocument();
  });

  it("shows Pass/Partial distinctly in the table, and Escalated in its own review panel", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/1/report": READY_REPORT,
        "/check-runs/1/escalated": ESCALATED_ITEMS,
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    await screen.findByText("Ready");
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.queryByText("Fail")).not.toBeInTheDocument();
    // Never silently shown as a normal table row with no action available:
    expect(await screen.findByText("Needs your review (1)")).toBeInTheDocument();
    expect(screen.getByText('"The Glossary defines all key terms"')).toBeInTheDocument();
  });

  it("shows the evidence anchor next to its quote", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/1/report": READY_REPORT,
        "/check-runs/1/escalated": ESCALATED_ITEMS,
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    await screen.findByText("Ready");
    expect(screen.getByText("The real evidence sentence.")).toBeInTheDocument();
    expect(screen.getByText("page 3")).toBeInTheDocument();
  });

  it("shows the threshold explainer", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/1/report": READY_REPORT,
        "/check-runs/1/escalated": ESCALATED_ITEMS,
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    expect(await screen.findByText(/Ready because the score is 92.5%/)).toBeInTheDocument();
  });

  it("shows needs_review honestly with no fabricated score", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/1/report": NEEDS_REVIEW_REPORT }));
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    expect(await screen.findByText("Needs Review")).toBeInTheDocument();
    expect(screen.queryByText("null%")).not.toBeInTheDocument();
    expect(screen.getByText(/No criteria could be auto-scored/)).toBeInTheDocument();
  });

  it("expands to show additional evidence quotes on click", async () => {
    const twoQuotes = {
      ...READY_REPORT,
      results: [
        {
          ...READY_REPORT.results[1],
          evidence: [
            { quote: "First quote.", anchor: "page 3" },
            { quote: "Second quote.", anchor: "page 4" },
          ],
        },
      ],
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/1/report": twoQuotes }));
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    await screen.findByText("First quote.");
    expect(screen.queryByText("Second quote.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getByText("Second quote.")).toBeInTheDocument();
  });

  it("shows an error banner when the report fails to load", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/1/report": new Response(
          JSON.stringify({ error: { code: "conflict", message: "This check hasn't finished yet." } }),
          { status: 409 },
        ),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/1", path: "/report/:checkRunId" });
    expect(await screen.findByRole("alert")).toHaveTextContent("This check hasn't finished yet.");
  });
});
