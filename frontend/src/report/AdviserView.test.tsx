import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut, ReportOut, SharedReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AdviserViewPage } from "./AdviserView";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_group_label: "G-Adviser",
  manuscript_original_filename: null,
  rubric_title: "TIP Format",
  status: "conditionally_ready",
  composite_score: 72,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: null,
  flag_deduction: 0,
  unresolved_high_flag_count: 0,
  decision: null,
  decided_at: null,
  decision_note: null,
  pending_review_count: 1,
  rubric_is_current: true,
  llm_mode: "real",
  rubric_needs_review: false,
  rubric_parse_issues: null,
  previous_status: null,
  previous_composite_score: null,
  results: [
    {
      criterion_id: 1,
      text: "Has an abstract",
      type: "structural",
      weight: 50,
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
      text: "States the research problem",
      type: "semantic",
      weight: 50,
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
  ],
};

const SAMPLE_FLAG: FlagSummaryOut = {
  id: 9,
  check_kind: "internal_agreement",
  severity: "med",
  criterion_text: null,
  evidence_excerpt: "The abstract claims 95% accuracy, Chapter 4 reports 87%.",
  page_anchor: "page 3",
  overridden: false,
};

function shared(overrides: Partial<SharedReportOut> = {}): SharedReportOut {
  return { report: BASE_REPORT, flags: [], ...overrides };
}

describe("AdviserViewPage", () => {
  // Global `afterEach(cleanup)` (src/test/setup.ts) already unmounts the
  // component after every test, which runs useNoindexMeta's own effect
  // cleanup -- a second, manual removal here raced it and threw
  // "not a child of this node" on whichever ran second.
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function render() {
    return renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123", path: "/shared/:token" });
  }

  it("shows a loading state, then the report", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    expect(screen.getByText("Loading shared report.")).toBeInTheDocument();
    expect((await screen.findAllByText("Conditionally Ready")).length).toBeGreaterThan(0);
  });

  it("BUG-049: discloses a test-mode (fake-LLM) run to the adviser -- the audience with no other context at all", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/shared/tok123/report": shared({ report: { ...BASE_REPORT, llm_mode: "fake" } }) }),
    );
    render();
    expect(await screen.findByText(/Test-mode run/)).toBeInTheDocument();
  });

  it("shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Conditionally Ready");
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("BUG-049 (backend-critic finding): discloses an unknown-mode run to the adviser, distinctly from real", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": shared({ report: { ...BASE_REPORT, llm_mode: "unknown" } }),
      }),
    );
    render();
    expect(await screen.findByText(/AI mode unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("BUG-052: discloses a rubric activated with a coverage warning to the adviser too", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": shared({
          report: {
            ...BASE_REPORT,
            rubric_needs_review: true,
            rubric_parse_issues: ["Only 10% of the source text is reflected."],
          },
        }),
      }),
    );
    render();
    expect(
      await screen.findByText(/activated while the parser's own coverage check/),
    ).toBeInTheDocument();
  });

  it("BUG-044: never renders a prior run's status/score, even if a mock response smuggles it in", async () => {
    // The real fix is that `PublicReportOut` doesn't carry these fields at
    // all -- this proves the FRONTEND half independently: even a raw JSON
    // response (bypassing TS entirely, the way a real fetch response
    // would) that still had the old fields must not surface them, since
    // this component no longer reads `report.previous_status` at all.
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/shared/tok123/report") {
        return new Response(
          JSON.stringify({
            report: { ...BASE_REPORT, previous_status: "not_ready", previous_composite_score: 26.67 },
            flags: [],
          }),
          { status: 200 },
        );
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render();
    await screen.findAllByText("Conditionally Ready");
    expect(screen.queryByText("Previously")).not.toBeInTheDocument();
    expect(screen.queryByText(/26\.67/)).not.toBeInTheDocument();
  });

  it("renders the full, unfiltered results table -- pending criteria show as Awaiting review, not hidden", async () => {
    // Mobile card + desktop grid both render in the DOM simultaneously
    // (jsdom applies no media queries) -- same convention as every other
    // dual-layout test in this app: text present in both is expected
    // TWICE, not once.
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();

    expect((await screen.findAllByText("Has an abstract")).length).toBe(2);
    // ui-designer P1: the escalated criterion must be VISIBLE, not
    // silently dropped the way Report.tsx's own filtered subset would.
    expect(screen.getAllByText("States the research problem").length).toBe(2);
    expect(screen.getAllByText("Awaiting review").length).toBe(2);
    // Rendered as part of a combined caption ("AI-graded · The instructor
    // has not resolved this criterion yet."), not its own isolated node.
    expect(
      screen.getAllByText(/The instructor has not resolved this criterion yet\./).length,
    ).toBe(2);
  });

  it("shows the pending-review sentence in the explainer when items are still unresolved", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    expect(
      await screen.findByText(/1 criterion still needs the instructor's review/),
    ).toBeInTheDocument();
  });

  it("renders flags with no link-to-detail control (auth-gated route, would just bounce)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/shared/tok123/report": shared({ flags: [SAMPLE_FLAG] }) }),
    );
    render();
    await screen.findByText(/95% accuracy/);
    expect(screen.queryByRole("link", { name: /Review evidence|View details/ })).not.toBeInTheDocument();
  });

  it("renders a read-only decision summary with no decide controls", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Has an abstract");
    expect(
      screen.getByText("The instructor has not made a final decision on this report yet."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve for defense/ })).not.toBeInTheDocument();
  });

  it("shows a decided report's decision, note, and no mutating controls", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": shared({
          report: {
            ...BASE_REPORT,
            decision: "approved",
            decided_at: "2026-08-15T12:00:00Z",
            decision_note: "Looks ready.",
          },
        }),
      }),
    );
    render();
    expect(await screen.findByText("Approved for defense")).toBeInTheDocument();
    expect(screen.getByText(/Decided by the instructor on/)).toBeInTheDocument();
    expect(screen.getByText("Looks ready.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reopen this decision" })).not.toBeInTheDocument();
  });

  it("shows an honest 404 message, distinct from a 410", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": new Response(
          JSON.stringify({ error: { code: "not_found", message: "This link doesn't exist." } }),
          { status: 404 },
        ),
      }),
    );
    render();
    expect(await screen.findByText("Link not found")).toBeInTheDocument();
    expect(screen.getByText("This link doesn't exist.")).toBeInTheDocument();
    expect(screen.getByText(/contact the instructor who sent you this link/)).toBeInTheDocument();
  });

  it("shows an honest 410 message when the link was revoked", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": new Response(
          JSON.stringify({
            error: { code: "gone", message: "This link has been turned off by the instructor." },
          }),
          { status: 410 },
        ),
      }),
    );
    render();
    expect(await screen.findByText("Link no longer available")).toBeInTheDocument();
    expect(
      screen.getByText("This link has been turned off by the instructor."),
    ).toBeInTheDocument();
  });

  it("adds a noindex meta tag on mount and removes it on unmount", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    const { unmount } = render();
    await waitFor(() => {
      const meta = document.querySelector('meta[name="robots"]');
      expect(meta).not.toBeNull();
      expect(meta?.getAttribute("content")).toBe("noindex, nofollow");
    });
    unmount();
    expect(document.querySelector('meta[name="robots"]')).toBeNull();
  });

  it("discloses the semi-confidential / not-indexed framing to the reader", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Has an abstract");
    expect(screen.getByText(/Read-only shared view/)).toBeInTheDocument();
    expect(screen.getByText(/isn't indexed by search engines/)).toBeInTheDocument();
  });
});
