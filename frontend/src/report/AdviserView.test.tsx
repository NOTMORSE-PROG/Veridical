import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut, PublicCriterionResultOut, ReportOut, SharedReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AdviserViewPage } from "./AdviserView";

const BASE_REPORT: ReportOut = {
  assessment_revision: 0,
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

function publicCountPayload(outcomes: PublicCriterionResultOut["outcome"][]): SharedReportOut {
  return { report: {
    check_run_id: 5, manuscript_group_label: "Synthetic shared-count control", manuscript_original_filename: null,
    rubric_title: "Synthetic criteria", status: "needs_review", composite_score: null, thresholds: BASE_REPORT.thresholds,
    reason: "Review the recorded criteria and assessment limits.", flag_deduction: 0, unresolved_high_flag_count: 0,
    decision: null, decided_at: null, decision_note: null, rubric_is_current: true, llm_mode: "fake",
    rubric_needs_review: false, rubric_parse_issues: null,
    results: outcomes.map((outcome, index) => ({
      criterion_id: index + 1, text: `Shared criterion ${index + 1}`, type: "semantic", kind: "semantic",
      weight: 1, weight_importance: "med", outcome, score: outcome === "passed" ? 100 : outcome === "failed" ? 0 : null,
      basis: "llm", anchor: null, reasoning: null, reason: null, evidence: [],
      resolved: outcome === "passed" || outcome === "failed" || outcome === "not_applicable",
    })),
  }, flags: [] };
}

describe("AdviserViewPage", () => {
  it("keeps all public qualifications open in one context and puts records before findings", async () => {
    const payload = publicCountPayload(["passed", "escalated"]);
    Object.assign(payload.report, { rubric_needs_review: true,
      rubric_parse_issues: ["SECONDARY_FORMAT_DETAIL", "SECONDARY_FORMAT_DETAIL"], assessment_incomplete: true,
      semantic_support_unverified: true, historical_native_math_unverified: true,
      historical_citation_title_unverified: true });
    const fetch = stubFetchByPath({ "/shared/tok123/report": payload });
    vi.stubGlobal("fetch", fetch);
    render();
    const context = await screen.findByRole("complementary", { name: "Assessment context" });
    for (const title of ["Test-mode AI responses", "Required format review", "Assessment limits", "AI evidence support needs review", "Saved native Word coverage is unverified", "Historical title comparison needs review"]) {
      expect(screen.getByText(title)).toBeVisible();
      expect(context).toContainElement(screen.getByText(title));
    }
    expect(screen.getAllByText("Needs Review")).toHaveLength(1);
    expect(screen.getAllByText("SECONDARY_FORMAT_DETAIL")).toHaveLength(2);
    expect(screen.getAllByText("SECONDARY_FORMAT_DETAIL")[0]).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show required-format review details" }));
    expect(screen.getAllByText("SECONDARY_FORMAT_DETAIL")[0]).toBeVisible();
    const criteria = screen.getByRole("heading", { name: "Criterion records" });
    const findings = screen.getByRole("heading", { name: "Possible inconsistencies" });
    expect(criteria.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("link", { name: "Review criterion records" })).toHaveAttribute("href", "/shared/tok123#criteria-results-heading");
    expect(screen.getByRole("link", { name: "Read final decision" })).toHaveAttribute("href", "/shared/tok123#shared-decision-heading");
    expect(fetch.mock.calls.every(([input]) => String(input).includes("/shared/tok123/report"))).toBe(true);
  });
  it("preserves public query context and focuses the actual destination headings", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?reader=faculty", path: "/shared/:token" });
    const records = await screen.findByRole("link", { name: "Review criterion records" });
    expect(records).toHaveAttribute("href", "/shared/tok123?reader=faculty#criteria-results-heading");
    fireEvent.click(records);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Criterion records" })).toHaveFocus());
    fireEvent.click(screen.getByRole("link", { name: "Read final decision" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Final decision" })).toHaveFocus());
  });

  it("omits context when no public qualifier applies without inferring successful checks", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findByRole("heading", { name: "Criterion records" });
    expect(screen.queryByRole("complementary", { name: "Assessment context" })).not.toBeInTheDocument();
    expect(screen.getByText("This run produced no shared integrity signals.")).toBeVisible();
    expect(screen.queryByText(/All checks passed/)).not.toBeInTheDocument();
  });

  it.each([
    ["escalated", 1], ["unverifiable", 1], ["quota_exhausted", 1], ["api_down", 1],
    ["passed", 0], ["failed", 0], ["not_applicable", 0],
  ] as const)("counts only unresolved public criterion outcome %s", async (outcome, count) => {
    const payload = publicCountPayload([outcome]);
    expect(payload.report).not.toHaveProperty("pending_review_count");
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": payload }));
    render();
    const label = await screen.findByText("Unresolved criteria");
    expect(label.nextElementSibling).toHaveTextContent(String(count));
  });

  it("counts all four unresolved outcomes among resolved rows without a private count", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": publicCountPayload([
      "passed", "unverifiable", "failed", "escalated", "not_applicable", "api_down", "quota_exhausted",
    ]) }));
    render();
    expect((await screen.findByText("Unresolved criteria")).nextElementSibling).toHaveTextContent("4");
  });

  it("keeps the criterion count at zero with an open High flag and assessment limits", async () => {
    const payload = publicCountPayload(["passed", "failed", "not_applicable"]);
    payload.flags = [{ ...SAMPLE_FLAG, severity: "high" }];
    payload.report.assessment_incomplete = true;
    payload.report.unresolved_high_flag_count = 1;
    payload.report.status = "not_ready";
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": payload }));
    render();
    expect((await screen.findByText("Unresolved criteria")).nextElementSibling).toHaveTextContent("0");
    expect(screen.getByText("Assessment limits")).toBeInTheDocument();
    expect(screen.getByText(/95% accuracy/)).toBeInTheDocument();
  });

  it("uses the newly projected resolved outcome when the shared report is reopened", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": publicCountPayload(["escalated"]) }));
    const first = render();
    expect((await screen.findByText("Unresolved criteria")).nextElementSibling).toHaveTextContent("1");
    first.unmount();
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": publicCountPayload(["passed"]) }));
    render();
    expect((await screen.findByText("Unresolved criteria")).nextElementSibling).toHaveTextContent("0");
  });
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

  it.each([undefined, false, true])("shows only the allowlisted semantic support caution when flagged=%s", async (flag) => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: { ...BASE_REPORT, semantic_support_unverified: flag } }) }));
    render();
    await screen.findAllByText("Conditionally Ready");
    if (flag) {
      expect(screen.getByText("AI evidence support needs review")).toBeVisible();
      expect(screen.getByText("Ask the instructor for manuscript access or clarification if needed.")).toBeVisible();
    } else expect(screen.queryByText("AI evidence support needs review")).not.toBeInTheDocument();
    expect(screen.queryByText(/suggestions predate criterion-support/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Open in manuscript/ })).not.toBeInTheDocument();
  });

  it("discloses the historical title limitation in both the shared report and its finding", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: { ...BASE_REPORT, historical_citation_title_unverified: true }, flags: [{ ...SAMPLE_FLAG, check_kind: "citation_integrity", problem_kind: "possible_metadata_mismatch", citation_metadata_review_warning: "quoted_title_projection_unverified" }] }) }));
    render();
    expect((await screen.findAllByText("Historical title comparison needs review")).length).toBeGreaterThan(1);
    expect(screen.getByText(/this report has not been recomputed/)).toBeInTheDocument();
    expect(screen.getByText(/may include publication details/)).toHaveTextContent("Verify affected titles before relying on those differences; this report has not been recomputed.");
    expect(screen.queryByText(/Review the warning on each affected finding/)).not.toBeInTheDocument();
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
    expect(await screen.findByText("Test-mode AI responses")).toBeInTheDocument();
    expect(screen.getByText("These fixture responses are not a real manuscript assessment.")).toBeVisible();
    expect(screen.queryByText(/Fixture responses produced this report/)).not.toBeInTheDocument();
  });

  it("shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findAllByText("Conditionally Ready");
    expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
  });

  it("discloses incomplete coverage without publishing private check details", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/shared/tok123/report": shared({ report: { ...BASE_REPORT, assessment_incomplete: true } }),
    }));
    render();
    expect(await screen.findByText("Assessment limits")).toBeInTheDocument();
    expect(screen.getByText(/Unchecked content is not treated as passed/)).toBeInTheDocument();
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
    expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
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
      await screen.findByText("Required format review"),
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

    expect((await screen.findAllByText("Has an abstract")).length).toBe(1);
    // design review P1: the escalated criterion must be VISIBLE, not
    // silently dropped the way Report.tsx's own filtered subset would.
    expect(screen.getAllByText("States the research problem").length).toBe(1);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    // Rendered as part of a combined caption ("AI-graded · The instructor
    // has not resolved this criterion yet."), not its own isolated node.
  });

  it("shows the pending-review sentence in the explainer when items are still unresolved", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    expect(
      await screen.findByText("Unresolved criteria"),
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
      screen.getByText("No final instructor decision has been recorded."),
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
