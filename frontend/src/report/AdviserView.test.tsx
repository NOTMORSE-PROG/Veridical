import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut, ReportOut, SharedReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AdviserViewPage } from "./AdviserView";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_id: 5,
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
  integrity_check_status: [],
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
  is_passage_level: false,
  first_upload_context: false,
  confirmed_citation_source: false,
  problem_kind: null,
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

  it("keeps private teacher fields hidden even if a response includes them", async () => {
    const result = { ...BASE_REPORT.results[0], reasoning: "Public source explanation", resolved: true,
      resolution: { type: "mark_level" as const, reason: "PRIVATE grading comment", ai_majority_verdict: null },
      annotation: "PRIVATE note" };
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: {
      ...BASE_REPORT, results: [result],
    } }) }));
    render();
    expect(await screen.findByText("Public source explanation")).toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Members \/ authors:/)).not.toBeInTheDocument();
    expect(screen.getByTitle("Medium importance")).toBeInTheDocument();
  });

  it("shows a loading state, then the report", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    expect(screen.getByText("Loading shared report…")).toBeInTheDocument();
    expect((await screen.findAllByText("Conditionally Ready")).length).toBeGreaterThan(0);
    expect(screen.queryByText("72%")).not.toBeInTheDocument();
  });

  it("BUG-049: discloses a test-mode (fake-LLM) run to the adviser -- the audience with no other context at all", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/shared/tok123/report": shared({ report: { ...BASE_REPORT, llm_mode: "fake" } }) }),
    );
    render();
    expect(await screen.findByText("Test-mode AI results")).toBeInTheDocument();
  });

  it("shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Conditionally Ready");
    expect(screen.queryByText("Test-mode AI results")).not.toBeInTheDocument();
  });

  it("keeps a source-reviewed report free of model-result notices", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: { ...BASE_REPORT, llm_mode: "manual" } }) }));
    render();
    await screen.findAllByText("Conditionally Ready");
    expect(screen.queryByText("Test-mode AI results")).not.toBeInTheDocument();
    expect(screen.queryByText("AI mode could not be verified")).not.toBeInTheDocument();
  });

  it("BUG-049 (backend review finding): discloses an unknown-mode run to the adviser, distinctly from real", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/shared/tok123/report": shared({ report: { ...BASE_REPORT, llm_mode: "unknown" } }),
      }),
    );
    render();
    expect(await screen.findByText("AI mode could not be verified")).toBeInTheDocument();
    expect(screen.queryByText("Test-mode AI results")).not.toBeInTheDocument();
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
      await screen.findByText("The required format had parser uncertainty"),
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

  it("keeps every criterion selectable, including ungraded criteria", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findByRole("navigation", { name: "Choose a criterion" });
    const choice = screen.getByRole("button", { name: /States the research problem/ });
    expect(choice).toHaveTextContent("Needs review");
    fireEvent.click(choice);
    expect(screen.getByRole("heading", { name: "States the research problem" })).toBeInTheDocument();
    expect(screen.getByText("Recorded grade: Needs review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Has an abstract/ })).toBeInTheDocument();
  });

  it("shows the pending-review sentence in the explainer when items are still unresolved", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    expect(
      await screen.findByText("Criteria awaiting a teacher’s grade"),
    ).toBeInTheDocument();
  });

  it("renders flags with no link-to-detail control (auth-gated route, would just bounce)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/shared/tok123/report": shared({ flags: [SAMPLE_FLAG] }) }),
    );
    render();
    await screen.findByRole("button", { name: "Findings" });
    fireEvent.click(screen.getByRole("button", { name: "Findings" }));
    await screen.findByText(/95% accuracy/);
    expect(screen.queryByRole("link", { name: /Review evidence|View details/ })).not.toBeInTheDocument();
  });

  it("renders a read-only decision summary with no decide controls", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Has an abstract");
    expect(
      screen.getByText("No final instructor decision has been recorded"),
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
    expect((await screen.findAllByText("Approved for defense")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Recorded by the instructor/)).toBeInTheDocument();
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
    expect(screen.getByText(/Contact the instructor who sent this link/)).toBeInTheDocument();
    // BUG-069 item 5: must not claim "you're viewing a report shared by
    // the instructor" when there is, in fact, no report to view.
    expect(screen.queryByText(/Read-only shared report/)).not.toBeInTheDocument();
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
    expect(screen.queryByText(/Read-only shared report/)).not.toBeInTheDocument();
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
    expect(screen.getByText(/Read-only shared report/)).toBeInTheDocument();
    expect(screen.getByText(/not indexed by search engines/)).toBeInTheDocument();
  });
});
