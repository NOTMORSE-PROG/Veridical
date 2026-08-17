import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  decision: null,
  decided_at: null,
  decision_note: null,
  pending_review_count: 0,
  rubric_is_current: true,
  llm_mode: "real",
  rubric_needs_review: false,
  rubric_parse_issues: null,
  previous_status: null,
  previous_composite_score: null,
  results: [
    {
      criterion_id: 1,
      text: "Manuscript contains an abstract of at most 250 words",
      type: "structural",
      weight: 16.667,
      weight_importance: "med",
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
      weight_importance: "med",
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

  it("BUG-049: discloses a test-mode (fake-LLM) run so its verdict is never mistaken for a real one", async () => {
    const report: ReportOut = { ...BASE_REPORT, llm_mode: "fake" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/Test-mode run/)).toBeInTheDocument();
  });

  it("shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("BUG-049 (backend-critic finding): discloses an unknown-mode run distinctly from real, never silently as real", async () => {
    const report: ReportOut = { ...BASE_REPORT, llm_mode: "unknown" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/AI mode unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("BUG-052: discloses a rubric that was activated with a coverage warning", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      rubric_needs_review: true,
      rubric_parse_issues: ["Only 10% of the source text is reflected in the parsed criteria."],
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/activated while the parser's own coverage check/)).toBeInTheDocument();
    expect(screen.getByText(/Only 10% of the source text/)).toBeInTheDocument();
  });

  it("shows no rubric-coverage disclosure when the parser had no complaint", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.queryByText(/activated while the parser's own coverage check/)).not.toBeInTheDocument();
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
    // D-023: weight renders as a Low/Medium/High importance tag, never a
    // raw percentage.
    expect(screen.getAllByText("Medium").length).toBeGreaterThan(0);
    expect(screen.queryByText(/16\.7%/)).not.toBeInTheDocument();
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
          weight_importance: "med",
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
          weight_importance: "med",
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
          weight_importance: "med",
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

  it("V-038: the header link reflects undecided vs decided state and points at the decision section", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const link = await screen.findByRole("link", { name: "Go to final decision" });
    expect(link).toHaveAttribute("href", "#decision-heading");
  });

  it("V-038: a decided report's header link states the actual decision, not the generic prompt", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      decision: "approved",
      decided_at: "2026-08-13T10:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("link", { name: "Decision: Approved for defense" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to final decision" })).not.toBeInTheDocument();
  });

  it("V-038: the decision panel renders at the end of the report, wired to the real check_run_id", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("heading", { name: "Final decision" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve for defense" })).toBeEnabled();
  });

  it("ux-critic finding: deciding through the REAL confirm modal moves focus to the Final decision heading once it closes, not just in an isolated rerender", async () => {
    const decided: ReportOut = { ...BASE_REPORT, decision: "approved", decided_at: "2026-08-13T10:00:00Z" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/report": BASE_REPORT,
        "/check-runs/5/decision": decided,
        ...stubEscalated(),
        ...stubFlags(),
      }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Approve for defense" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve for defense" }));

    // The real Modal unmounts (its own focus-restore cleanup fires),
    // DecisionPanel re-renders from the live query cache with the newly
    // decided report, and its own transition effect claims focus -- this
    // exercises the actual integrated path end to end, not a bypassed
    // prop rerender.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Final decision" })),
    );
  });

  it("V-041: renders the version-comparison line when a real prior run exists", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      status: "ready",
      composite_score: 95,
      previous_status: "conditionally_ready",
      previous_composite_score: 70,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": report, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Previously")).toBeInTheDocument();
    expect(screen.getByText("Conditionally Ready")).toBeInTheDocument();
    expect(screen.getByText("(70%)")).toBeInTheDocument();
    expect(screen.getByText("now")).toBeInTheDocument();
    expect(screen.getByText("(95%)")).toBeInTheDocument();
  });

  it("V-041: omits the version-comparison line for a manuscript's first-ever report", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(screen.queryByText("Previously")).not.toBeInTheDocument();
  });

  it("V-039: Export PDF fetches the real PDF endpoint and triggers a save, with a loading state", async () => {
    let resolveExport: ((r: Response) => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/report/export.pdf") {
        return new Promise<Response>((resolve) => {
          resolveExport = resolve;
        });
      }
      const handlers = {
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags(),
      } as Record<string, unknown>;
      return new Response(JSON.stringify(handlers[path] ?? {}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL.createObjectURL", vi.fn(() => "blob:mock"));
    vi.stubGlobal("URL.revokeObjectURL", vi.fn());
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));

    expect(await screen.findByRole("button", { name: /Exporting PDF/ })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    resolveExport?.(
      new Response(new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])]), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Export PDF" })).not.toHaveAttribute("aria-busy"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/check-runs/5/report/export.pdf"),
      expect.anything(),
    );
  });

  it("V-039: ux-critic finding (P1): rapid double-clicking Export PDF fires only ONE export request", async () => {
    // `disabled={isPending}` only takes effect after React's next commit
    // -- N synchronous clicks landing in the same tick would otherwise
    // all call mutate(), each one a real server-side reportlab render on
    // a single-worker, 512MB Render instance (the exact concurrent-
    // render risk this ticket's own research picked reportlab to avoid).
    let exportCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/report/export.pdf") {
        exportCalls += 1;
        return new Response(new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        });
      }
      const handlers = {
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags(),
      } as Record<string, unknown>;
      return new Response(JSON.stringify(handlers[path] ?? {}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL.createObjectURL", vi.fn(() => "blob:mock"));
    vi.stubGlobal("URL.revokeObjectURL", vi.fn());
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    const button = screen.getByRole("button", { name: "Export PDF" });
    // Three clicks in the same synchronous tick, before React commits
    // the disabled state -- reproduces the real rapid-click race.
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(exportCalls).toBeGreaterThan(0));
    expect(exportCalls).toBe(1);
  });

  it("V-039: discloses the exported PDF's screen-reader limitation where a real user would see it", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/report": BASE_REPORT, ...stubEscalated(), ...stubFlags() }),
    );
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    expect(
      screen.getByText(/Downloaded PDFs are not optimized for screen readers/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export PDF" })).toHaveAttribute(
      "aria-describedby",
      "export-pdf-note",
    );
  });

  it("V-039: shows the server's error if the export request fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/report/export.pdf") {
        return new Response(
          JSON.stringify({ error: { code: "internal", message: "Could not build the export." } }),
          { status: 500 },
        );
      }
      const handlers = {
        "/check-runs/5/report": BASE_REPORT,
        ...stubEscalated(),
        ...stubFlags(),
      } as Record<string, unknown>;
      return new Response(JSON.stringify(handlers[path] ?? {}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Ready");
    fireEvent.click(screen.getByRole("button", { name: "Export PDF" }));

    const error = await screen.findByText("Could not build the export.");
    const alert = error.closest('[role="alert"]');
    expect(alert).toBeInTheDocument();
    // Focus moves to the error itself (ReopenModal.tsx/DecisionModal.tsx's
    // own established convention) -- a passive live-region announcement
    // alone isn't enough for a screen-reader user who isn't listening at
    // the exact moment the request settles.
    expect(document.activeElement).toBe(alert);
    // The button must recover, not stay stuck busy on a failed export.
    expect(screen.getByRole("button", { name: "Export PDF" })).not.toHaveAttribute("aria-busy");
  });
});
