import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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

describe("shared statistical applicability", () => {
  it.each([
    ["completed", "Completion recorded."],
    ["not_applicable", "Not applicable."],
    ["incomplete", "Not fully assessed."],
  ] as const)("shows safe %s status alongside the existing shared warning", async (state, label) => {
    const payload = shared({ report: { ...BASE_REPORT, statistical_check_scope: { state }, historical_native_math_unverified: true } });
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/scope-control/report": payload }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/scope-control?section=findings&module=statistical_forensics", path: "/shared/:token" });
    expect(await screen.findByText(`Statistical checks: ${label}`)).toBeVisible();
    expect(screen.getAllByText("Saved native Word coverage is unverified")[0]).toBeVisible();
  });
});

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
  it("leads with the recorded manuscript title and members without a duplicate identity heading", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: {
      ...BASE_REPORT, subject: { basis: "confirmed_assignment_at_check", title: "Recorded manuscript title", member_names: ["First Member", "Second Member"] },
    } }) }));
    render();
    expect(await screen.findByRole("heading", { level: 1, name: "Recorded manuscript title" })).toBeVisible();
    expect(screen.getByText("Group members: First Member, Second Member.")).toBeVisible();
    expect(screen.getByText("Required format: TIP Format")).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "Recorded manuscript title" })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Conditionally Ready" })).toBeVisible();
    expect(screen.getByText("Not recorded", { selector: "dd" })).toBeVisible();
  });

  it("does not promote unrecorded title or members into historical shared identity", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ report: {
      ...BASE_REPORT, subject: { basis: "not_recorded", title: "Unconfirmed title", member_names: ["Unconfirmed member"] },
    } }) }));
    render();
    expect(await screen.findByRole("heading", { level: 1, name: "Readiness report" })).toBeVisible();
    expect(screen.queryByText("Unconfirmed title")).not.toBeInTheDocument();
    expect(screen.queryByText(/Unconfirmed member/)).not.toBeInTheDocument();
  });

  it("keeps all public qualifications open while switching between grades and findings", async () => {
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
    expect(screen.getByRole("heading", { name: "Criterion records" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Possible inconsistencies" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Findings" }));
    expect(screen.getByRole("heading", { name: "Possible inconsistencies" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Criterion records" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review criterion records" })).toHaveAttribute("href", "/shared/tok123?section=grades#criteria-results-heading");
    expect(screen.getByRole("link", { name: "Read final decision" })).toHaveAttribute("href", "/shared/tok123?section=findings#shared-decision-heading");
    expect(fetch.mock.calls.every(([input, init]) => /\/shared\/tok123\/(report|evidence)$/.test(String(input)) && init?.credentials === "omit")).toBe(true);
  });
  it("preserves public query context and focuses the actual destination headings", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?reader=faculty", path: "/shared/:token" });
    const records = await screen.findByRole("link", { name: "Review criterion records" });
    expect(records).toHaveAttribute("href", "/shared/tok123?reader=faculty&section=grades#criteria-results-heading");
    records.focus();
    fireEvent.click(records);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Criterion records" })).toHaveFocus());
    const decision = screen.getByRole("link", { name: "Read final decision" });
    decision.focus(); fireEvent.click(decision);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Final decision" })).toHaveFocus());
  });

  it("omits context when no public qualifier applies without inferring successful checks", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();
    await screen.findByRole("heading", { name: "Criterion records" });
    expect(screen.queryByRole("complementary", { name: "Assessment context" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Findings" }));
    expect(screen.getByText("No shared integrity signals are shown in this module.")).toBeVisible();
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
    fireEvent.click(screen.getByRole("tab", { name: "Findings" }));
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
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?section=findings&module=citation_integrity", path: "/shared/:token" });
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
    expect(await screen.findByText("AI response mode could not be verified")).toBeInTheDocument();
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

  it("keeps every criterion selectable, including pending records, with one full assessment at a time", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared() }));
    render();

    const chooser = await screen.findByRole("navigation", { name: "Choose a shared criterion" });
    expect(within(chooser).getByRole("button", { name: /Has an abstract/ })).toBeVisible();
    const pending = within(chooser).getByRole("button", { name: /States the research problem/ });
    expect(pending).toBeVisible();
    expect(screen.getByRole("heading", { name: "Has an abstract" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "States the research problem" })).not.toBeInTheDocument();
    fireEvent.click(pending);
    expect(screen.getByRole("heading", { name: "States the research problem" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Has an abstract" })).not.toBeInTheDocument();
    expect(within(chooser).getByRole("button", { name: /States the research problem/ })).toHaveTextContent(/Awaiting review/i);
    expect(document.querySelectorAll(".signal-result-card")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next criterion" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous criterion" }));
    expect(screen.getByRole("heading", { name: "Has an abstract" })).toBeVisible();
  });

  it("keeps every grouped finding location reachable while mounting one complete detail packet", async () => {
    const locations: FlagSummaryOut[] = Array.from({ length: 39 }, (_, index) => ({ ...SAMPLE_FLAG,
      id: index + 10, check_kind: "originality_reuse", problem_kind: "passage_overlap", matched_ref: 7,
      evidence_excerpt: `Complete own quotation ${index + 1}`, page_anchor: `page ${index + 1}`,
    }));
    const other = { ...SAMPLE_FLAG, id: 99, check_kind: "originality_reuse" as const, evidence_excerpt: "Separate saved quotation" };
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({
      flags: [...locations, other], detail_snapshot: "current",
      finding_details: [...locations, other].map(flag => ({ id: flag.id, ai_verdict_summary: null,
        ai_reasoning: `Complete explanation ${flag.id}`,
        passage_pair: { own_excerpt: flag.evidence_excerpt, matched_excerpt: `Complete comparison quotation ${flag.id}`,
          own_context_before: null, own_context_after: null, matched_ref: 7, matched_context_before: null,
          matched_context_after: null, context_words_each_side: 0, similarity: 0.8, level: "high_similarity" },
      })),
    }) }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?section=findings&module=originality_reuse&finding=48&reader=faculty", path: "/shared/:token" });
    expect(await screen.findByText("2 review items in this module, across 40 saved records. Choose an item to read its explanation and evidence.")).toBeVisible();
    expect(within(screen.getByRole("combobox", { name: "Choose a finding or comparison lead" })).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText("Complete explanation 48")).toBeVisible();
    expect(screen.getByText("Complete comparison quotation 48")).toBeVisible();
    expect(screen.queryByText("Complete explanation 10")).not.toBeInTheDocument();
    expect(screen.getAllByRole("article", { name: /Saved finding detail/ })).toHaveLength(1);
    expect(screen.getByText(/Location 39 of 39/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Next location" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous location" }));
    expect(screen.getByText("Complete explanation 47")).toBeVisible();
    expect(screen.queryByText("Complete comparison quotation 48")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next item" }));
    expect(screen.getByText("Complete explanation 99")).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Shared finding locations" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read final decision" })).toHaveAttribute("href", "/shared/tok123?section=findings&module=originality_reuse&finding=99&reader=faculty#shared-decision-heading");
    fireEvent.click(screen.getByRole("button", { name: /Citation integrity/ }));
    expect(screen.getByText("No shared integrity signals are shown in this module.")).toBeVisible();
    expect(screen.queryByRole("article", { name: /Saved finding detail/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Requested finding is unavailable/)).not.toBeInTheDocument();
  });

  it("preserves optional lead identity from the complete shared collection while showing one record", async () => {
    const leads = [30, 10, 20].map(id => ({ ...SAMPLE_FLAG, id, check_kind: "originality_reuse" as const,
      affects_readiness: false, is_passage_level: true, evidence_excerpt: `COMPLETE_LEAD_QUOTE_${id}` }));
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({ flags: leads }) }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?section=findings&module=originality_reuse&finding=20&lead_view=resolved", path: "/shared/:token" });
    const summary = await screen.findByLabelText(/Evidence for Comparison lead 2 of 3\. Record #20/);
    expect(summary).toBeVisible();
    expect(document.querySelectorAll(".signal-comparison-list > li")).toHaveLength(1);
    expect(screen.getByText("Selected record; 3 comparison leads in this check")).toBeVisible();
    expect(screen.queryByText("COMPLETE_LEAD_QUOTE_10", { selector: "blockquote" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Comparison lead 1 of 1/)).not.toBeInTheDocument();
    fireEvent.click(summary);
    expect(screen.getByText(/COMPLETE_LEAD_QUOTE_20/, { selector: "blockquote" })).toBeVisible();
    expect(screen.getByText("Not scored. Does not affect readiness or block your decision.")).toBeVisible();
    expect(screen.queryByRole("group", { name: "Filter comparison leads" })).not.toBeInTheDocument();
  });

  it("falls back visibly for a finding outside the selected module without showing another module's detail", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/shared/tok123/report": shared({
      flags: [SAMPLE_FLAG, { ...SAMPLE_FLAG, id: 88, check_kind: "citation_integrity", evidence_excerpt: "OTHER MODULE FULL TEXT" }],
      detail_snapshot: "current", finding_details: [{ id: 88, ai_reasoning: "OTHER MODULE EXPLANATION", ai_verdict_summary: null }],
    }) }));
    renderWithProviders(<AdviserViewPage />, { route: "/shared/tok123?section=findings&finding=88", path: "/shared/:token" });
    expect(await screen.findByText("Requested finding is unavailable in this module. Showing its first shared finding.")).toBeVisible();
    expect(screen.queryByText("OTHER MODULE FULL TEXT")).not.toBeInTheDocument();
    expect(screen.queryByText("OTHER MODULE EXPLANATION")).not.toBeInTheDocument();
    expect(screen.getByText(/95% accuracy/)).toBeVisible();
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
    await screen.findByRole("tab", { name: "Findings" });
    fireEvent.click(screen.getByRole("tab", { name: "Findings" }));
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
