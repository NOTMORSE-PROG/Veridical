import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut, ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ReportPage } from "./Report";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_group_label: "Ungrouped",
  manuscript_original_filename: null,
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

// BUG-033: every test renders <FlagsPanel>, which fetches this on mount —
// default to zero flags (the real, honest "found nothing" state) so
// existing tests exercise the actual success path rather than an
// unrelated 404/error alert stubFetchByPath's own fallback would
// otherwise render.
function stubFlags(items: FlagSummaryOut[] = []) {
  return { "/check-runs/5/flags": items };
}

const SAMPLE_FLAG: FlagSummaryOut = {
  id: 2,
  check_kind: "internal_agreement",
  severity: "med",
  criterion_text: null,
  evidence_excerpt: "The abstract claims a 95% accuracy rate, but Chapter 4 reports 87%.",
  page_anchor: "page 3",
  overridden: false,
};

describe("ReportPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the status pill, score, and manuscript/rubric identification", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("Ungrouped")).toBeInTheDocument();
    expect(screen.getByText("TIP Format")).toBeInTheDocument();
  });

  it("BUG-022: shows the manuscript's filename, not the (possibly default) group_label, once the report links a check to a specific upload", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      manuscript_group_label: "Ungrouped",
      manuscript_original_filename: "Chapter1-3_FinalDefense.pdf",
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Chapter1-3_FinalDefense.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Ungrouped")).not.toBeInTheDocument();
  });

  it("BUG (weight/criterion collision) regression guard: the results grid uses minmax(0,1fr) and weight is right-aligned, not overlapping", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
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
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(
      await screen.findByText(/2 unresolved high-severity flags found/),
    ).toBeInTheDocument();
  });

  it("states the actual determining factor for Not Ready via a low score, when there is no flag involved", async () => {
    const report: ReportOut = { ...BASE_REPORT, status: "not_ready", composite_score: 40 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
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
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
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
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.queryByText("Escalated criterion")).not.toBeInTheDocument();
    expect(screen.queryByText("Quota-exhausted criterion")).not.toBeInTheDocument();
  });

  it("BUG (anchor-loss) regression guard: a structural pass with an anchor but no quote still shows the anchor", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    // criterion_id 1 has evidence: [], reasoning: null, reason: null, anchor: "page 2"
    expect(screen.getAllByText("page 2").length).toBeGreaterThan(0);
  });

  it("shows Partial with a caption for a score-50 semantic pass, distinct from a full Pass", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
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
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
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
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }));
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
        if (url.includes("/flags")) return new Response("[]", { status: 200 });
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

  // BUG-033: before this, a real flag existed nowhere on the report an
  // instructor could reach it from — reproduced live, see the ticket.
  it("BUG-033: honestly says no flags were found when there are none", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/No integrity flags on this run/)).toBeInTheDocument();
  });

  it("BUG-033: renders a flag with a working link to its already-built detail page", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags([SAMPLE_FLAG]),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Flags (1)")).toBeInTheDocument();
    expect(
      screen.getByText(/The abstract claims a 95% accuracy rate/),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Review evidence" });
    expect(link).toHaveAttribute("href", "/flags/2");
  });

  it("BUG-033: an overridden flag reads distinctly from an active one — never the same", async () => {
    const overridden: FlagSummaryOut = { ...SAMPLE_FLAG, overridden: true };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags([overridden]),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("This flag was overridden.")).toBeInTheDocument();
    expect(screen.getByText("Overridden")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View details" })).toHaveAttribute("href", "/flags/2");
    expect(screen.queryByRole("link", { name: "Review evidence" })).not.toBeInTheDocument();
  });

  it("BUG-033: the explainer honestly names a real flag deduction and links down to the flags panel", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      status: "not_ready",
      composite_score: 26.67,
      flag_deduction: 40,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": report,
        ...stubEscalated(),
        ...stubFlags([SAMPLE_FLAG]),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(
      await screen.findByText(/This includes a 40-point deduction from unresolved flags/),
    ).toBeInTheDocument();
    const anchorLink = screen.getByRole("link", { name: "Flags" });
    expect(anchorLink).toHaveAttribute("href", "#flags-heading");
  });

  it("BUG-033/ux-critic finding: a group's collapsed-header severity scopes to UNRESOLVED flags only, never counting an overridden flag's severity as live risk", async () => {
    const overriddenHigh: FlagSummaryOut = { ...SAMPLE_FLAG, id: 30, severity: "high", overridden: true };
    const unresolvedLow: FlagSummaryOut = { ...SAMPLE_FLAG, id: 31, severity: "low", overridden: false };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags([overriddenHigh, unresolvedLow]),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Internal agreement (2)");
    // Must say "low", never "high" — the only unresolved flag is low
    // severity; the overridden high one is no longer live risk. Before
    // the fix this read "1 high, 1 low severity", implying an active
    // high-severity issue that didn't exist. Scoped to the group's own
    // toggle button (not the whole page) since the explainer sentence
    // elsewhere on screen legitimately says "no unresolved high-severity
    // flags" about the run as a whole.
    const groupToggle = screen.getByRole("button", { name: /Internal agreement \(2\)/ });
    expect(groupToggle).toHaveTextContent("1 low severity");
    expect(groupToggle).not.toHaveTextContent("high");
  });

  it("BUG-033/ux-critic finding: a manually-collapsed group survives a URL-restored remount (the real shape of a Back navigation)", async () => {
    const first: FlagSummaryOut = { ...SAMPLE_FLAG, id: 40 };
    const second: FlagSummaryOut = { ...SAMPLE_FLAG, id: 41 };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags([first, second]),
      }),
    );
    // Simulates the restored URL a real browser Back navigation would
    // land on after the instructor toggled this group closed and then
    // opened one flag's evidence — component state alone can't survive
    // this (FlagsPanel remounts), the URL must carry it.
    renderWithProviders(<ReportPage />, {
      route: "/report/5?flags_toggled=internal_agreement",
      path: "/report/:checkRunId",
    });

    // Both unresolved -> the group's COMPUTED default is open; the URL
    // param says the instructor toggled it away from that default, so
    // it must render collapsed instead.
    await screen.findByText("Internal agreement (2)");
    expect(screen.queryByText(/The abstract claims a 95% accuracy rate/)).not.toBeInTheDocument();
  });

  it("BUG-033: groups 2+ flags of the same check family under one disclosure, not separate panels", async () => {
    const second: FlagSummaryOut = {
      ...SAMPLE_FLAG,
      id: 3,
      evidence_excerpt: "A second, distinct internal-agreement issue.",
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags([SAMPLE_FLAG, second]),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Internal agreement (2)")).toBeInTheDocument();
    // Both unresolved -> the group defaults open (trust over scanability).
    expect(screen.getByText(/The abstract claims a 95% accuracy rate/)).toBeInTheDocument();
    expect(screen.getByText(/A second, distinct internal-agreement issue/)).toBeInTheDocument();
  });
});
