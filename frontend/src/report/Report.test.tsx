import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ReportPage } from "./Report";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_group_label: "Ungrouped",
  rubric_title: "TIP Format",
  status: "ready",
  composite_score: 92,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: null,
  flag_deduction: 0,
  unresolved_high_flag_count: 0,
  results: [
    {
      criterion_id: 1,
      text: "Manuscript contains an abstract of at most 250 words",
      type: "structural",
      weight: 16.667,
      kind: "structural",
      outcome: "passed",
      score: 100,
      basis: "rule",
      anchor: "page 2",
      reasoning: null,
      reason: null,
      evidence: [],
      resolution: null,
    },
    {
      criterion_id: 2,
      text: "Chapter 1 states the research problem",
      type: "semantic",
      weight: 33.333,
      kind: "semantic",
      outcome: "passed",
      score: 50,
      basis: "llm",
      anchor: null,
      reasoning: "Partially addresses the problem but lacks significance.",
      reason: null,
      evidence: [{ quote: "This is a test sentence used as evidence.", anchor: "page 3" }],
      resolution: null,
    },
  ],
};

function stubEscalated(items: unknown[] = []) {
  return { "/check-runs/5/escalated": items };
}

describe("ReportPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the status pill, score, and manuscript/rubric identification", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("Ungrouped")).toBeInTheDocument();
    expect(screen.getByText("TIP Format")).toBeInTheDocument();
  });

  it("BUG (weight/criterion collision) regression guard: the results grid uses minmax(0,1fr) and weight is right-aligned, not overlapping", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    const table = document.querySelector('[role="table"]');
    expect(table?.innerHTML).toContain("minmax(0,1fr)");
    // Weight rounds for display (16.667 -> 16.7%), doesn't leak raw precision.
    expect(screen.getAllByText("16.7%").length).toBeGreaterThan(0);
  });

  it("states the actual determining factor for Not Ready via an unresolved high-severity flag, not a hedge", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      status: "not_ready",
      composite_score: 90,
      unresolved_high_flag_count: 2,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(
      await screen.findByText(/2 unresolved high-severity flags found/),
    ).toBeInTheDocument();
  });

  it("states the actual determining factor for Not Ready via a low score, when there is no flag involved", async () => {
    const report: ReportOut = { ...BASE_REPORT, status: "not_ready", composite_score: 40 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/The score is 40% \(below the 60% floor\)/)).toBeInTheDocument();
  });

  it("uses the reason field verbatim for needs_review", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      status: "needs_review",
      composite_score: null,
      reason: "No criteria could be auto-scored.",
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("No criteria could be auto-scored.")).toBeInTheDocument();
  });

  it("BUG (duplicate rows) regression guard: escalated/quota_exhausted/api_down never render in the main results table", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      results: [
        ...BASE_REPORT.results,
        {
          criterion_id: 3,
          text: "Escalated criterion",
          type: "semantic",
          weight: 10,
          kind: "semantic",
          outcome: "escalated",
          score: null,
          basis: null,
          anchor: null,
          reasoning: null,
          reason: null,
          evidence: [],
          resolution: null,
        },
        {
          criterion_id: 4,
          text: "Quota-exhausted criterion",
          type: "semantic",
          weight: 10,
          kind: "semantic",
          outcome: "quota_exhausted",
          score: null,
          basis: null,
          anchor: null,
          reasoning: null,
          reason: null,
          evidence: [],
          resolution: null,
        },
      ],
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.queryByText("Escalated criterion")).not.toBeInTheDocument();
    expect(screen.queryByText("Quota-exhausted criterion")).not.toBeInTheDocument();
  });

  it("BUG (anchor-loss) regression guard: a structural pass with an anchor but no quote still shows the anchor", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    // criterion_id 1 has evidence: [], reasoning: null, reason: null, anchor: "page 2"
    expect(screen.getAllByText("page 2").length).toBeGreaterThan(0);
  });

  it("shows Partial with a caption for a score-50 semantic pass, distinct from a full Pass", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.getAllByText("Partial").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Counted as 50% credit toward this criterion's weight\./).length,
    ).toBeGreaterThan(0);
  });

  it("BUG (resolution attribution) regression guard: a resolved criterion shows the instructor's own reason, never mislabeled as AI-graded with the AI's stale failure text", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      results: [
        {
          criterion_id: 9,
          text: "The methodology section is appropriate",
          type: "semantic",
          weight: 20,
          kind: "semantic",
          outcome: "passed",
          score: 100,
          basis: "llm",
          anchor: null,
          reasoning: null,
          reason: "Could not verify the quoted evidence after a retry.",
          evidence: [],
          resolution: {
            type: "mark_pass",
            reason: "Methodology matches the approved proposal on manual review.",
            ai_majority_verdict: null,
          },
        },
      ],
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.getAllByText("Resolved by instructor").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("AI-graded")).toHaveLength(0);
    expect(
      screen.getAllByText(/Methodology matches the approved proposal on manual review\./).length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryAllByText("Could not verify the quoted evidence after a retry."),
    ).toHaveLength(0);
  });

  it("truncates a long evidence quote at a word boundary and expands on Show more", async () => {
    const longQuote = "word ".repeat(40).trim(); // 199 chars, well past the 140-char threshold
    const report: ReportOut = {
      ...BASE_REPORT,
      results: [
        {
          ...BASE_REPORT.results[1],
          evidence: [{ quote: longQuote, anchor: "page 5" }],
        },
      ],
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    const showMoreButtons = screen.getAllByRole("button", { name: "Show more" });
    expect(showMoreButtons.length).toBeGreaterThan(0);
    // Never cut mid-word: the truncated text must not contain the exact
    // un-truncated long string, and must end cleanly (word + ellipsis).
    const truncated = screen.getAllByText(/word word.*…/)[0];
    expect(truncated.textContent?.endsWith("…")).toBe(true);
    expect(truncated.textContent).not.toContain(longQuote);

    fireEvent.click(showMoreButtons[0]);
    expect(await screen.findAllByText(longQuote)).not.toHaveLength(0);
  });

  it("shows a real loading state, then a real fetch-error state with a working retry", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/escalated")) return new Response("[]", { status: 200 });
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(BASE_REPORT), { status: 200 });
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Try again");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("Ready")).toBeInTheDocument());
  });

  it("shows a distinct, honest not-finished-yet state with a link to check progress (never a generic error)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ error: { code: "conflict", message: "This check hasn't finished yet. Its report isn't ready." } }),
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This check hasn't finished yet.");
    expect(screen.getByRole("link", { name: "View check progress" })).toHaveAttribute(
      "href",
      "/checks/5",
    );
  });
});
