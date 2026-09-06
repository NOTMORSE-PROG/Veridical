import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscalatedItemOut, FlagSummaryOut, ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalReportPage } from "./SignalReport";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_group_label: "Group Syntax",
  manuscript_original_filename: "syntax-capstone.pdf",
  rubric_title: "T.I.P. Capstone Format",
  status: "ready",
  composite_score: 92,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: "The recorded outcomes place this manuscript in the Ready band.",
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
  integrity_check_status: [],
  results: [
    {
      criterion_id: 1,
      text: "Chapter 1 states the research problem",
      type: "semantic",
      weight: 33.333,
      weight_importance: "high",
      kind: "semantic",
      outcome: "passed",
      score: 100,
      basis: "llm",
      anchor: "page 3",
      reasoning: "The problem is stated and bounded.",
      reason: null,
      evidence: [{ quote: "This study addresses delayed laboratory access.", anchor: "page 3" }],
      resolution: null,
    },
  ],
};

const ESCALATED: EscalatedItemOut = {
  check_result_id: 44,
  criterion_id: 2,
  criterion_text: "The methodology explains participant selection",
  weight: 25,
  agreement: null,
  votes: ["pass", "fail"],
  ai_majority_verdict: null,
  reason: "The two grading passes disagreed.",
  review_reason: "low_confidence",
  unverified_evidence: null,
};

const FLAG: FlagSummaryOut = {
  id: 7,
  check_kind: "internal_agreement",
  severity: "high",
  criterion_text: null,
  evidence_excerpt: "The abstract and findings report different participant totals.",
  page_anchor: "pages 2 and 41",
  overridden: false,
  is_passage_level: false,
  first_upload_context: false,
  confirmed_citation_source: false,
  problem_kind: "numeric_mismatch",
};

function stubReport(report: ReportOut = BASE_REPORT, escalated: EscalatedItemOut[] = [], flags: FlagSummaryOut[] = []) {
  vi.stubGlobal("fetch", stubFetchByPath({
    "/check-runs/5/report": report,
    "/check-runs/5/escalated": escalated,
    "/check-runs/5/flags": flags,
    "/check-runs/5/share": null,
  }));
}

describe("SignalReportPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("leads with a readiness band and identity without presenting the composite as a judgment percentage", async () => {
    stubReport();
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect((await screen.findAllByText("Ready")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Group Syntax/)).toBeInTheDocument();
    expect(screen.getByText(/T\.I\.P\. Capstone Format/)).toBeInTheDocument();
    expect(screen.queryByText("92%")).not.toBeInTheDocument();
    expect(screen.getByText("High importance")).toBeInTheDocument();
  });

  it("moves focus to a report-order hash destination", async () => {
    stubReport(BASE_REPORT, [], [FLAG]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const target = await screen.findByRole("region", { name: "Integrity signals" });
    fireEvent.click(screen.getByRole("link", { name: /2\s*Inspect signals/ }));

    await waitFor(() => expect(target).toHaveFocus());
  });

  it("puts unresolved judgment first and blocks every final-decision action", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      status: "needs_review",
      pending_review_count: 1,
      results: [...BASE_REPORT.results, {
        criterion_id: 2,
        text: ESCALATED.criterion_text,
        type: "semantic",
        weight: 25,
        weight_importance: "med",
        kind: "semantic",
        outcome: "escalated",
        score: null,
        basis: "llm",
        anchor: null,
        reasoning: null,
        reason: ESCALATED.reason,
        evidence: [],
        resolution: null,
      }],
    };
    stubReport(report, [ESCALATED]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const firstTask = await screen.findByRole("heading", { name: "Criteria needing your judgment" });
    expect(firstTask).toBeInTheDocument();
    expect(screen.getByText(ESCALATED.criterion_text)).toBeInTheDocument();
    expect(screen.getByText("1 unresolved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve for defense" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Return for revision" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject manuscript" })).toBeDisabled();
  });

  it("makes a chosen-but-unconfirmed resolution legible as a pending step, not a done one (BUG-145)", async () => {
    stubReport(BASE_REPORT, [ESCALATED]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));

    // The chosen resolution is a real heading, not inline text -- and the
    // page states plainly that nothing is saved yet, so the two-step
    // choose-then-confirm flow can't be mistaken for a completed action
    // (the original bug: the options vanishing read exactly like success).
    expect(screen.getByRole("heading", { name: "Mark as meets criterion", level: 4 })).toBeInTheDocument();
    expect(screen.getByText("Step 2 of 2: nothing is saved yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm resolution" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    // Nothing was submitted by choosing -- the resolve mutation only fires on Confirm.
    expect(screen.getByText("1 unresolved")).toBeInTheDocument();

    // The pending state must be announced to assistive tech even though
    // autoFocus moves keyboard/AT focus straight into the reason textarea
    // on the same render (ux-critic finding: without this, a screen-reader
    // user never hears "nothing is saved yet" at all). A live region is
    // announced from its CONTENT, not its accessible name (which stays
    // empty with no aria-label), so this checks the role exists and wraps
    // the pending copy, not a computed name.
    const status = screen.getByRole("status");
    expect(within(status).getByText("Step 2 of 2: nothing is saved yet")).toBeInTheDocument();
    expect(within(status).getByText("Mark as meets criterion")).toBeInTheDocument();
  });

  it("returns focus to the criterion heading on Cancel, not to <body> (BUG-145)", async () => {
    stubReport(BASE_REPORT, [ESCALATED]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("heading", { name: ESCALATED.criterion_text })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Meets criterion" })).toBeInTheDocument();
  });

  it("shows bounded integrity evidence and honest partial-run and test-mode disclosures", async () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      llm_mode: "fake",
      rubric_needs_review: true,
      rubric_parse_issues: ["A table could not be transcribed reliably."],
      unresolved_high_flag_count: 1,
      integrity_check_status: [{
        check_kind: "citation_integrity",
        outcome: "api_down",
        n_skipped_quota: 0,
        n_skipped_api_down: 2,
        n_skipped_parse_failure: 0,
      }],
    };
    stubReport(report, [], [FLAG]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Test-mode AI results")).toBeInTheDocument();
    expect(screen.getByText("The required format had unresolved parser uncertainty")).toBeInTheDocument();
    expect(screen.getByText("Citation integrity was not fully assessed")).toBeInTheDocument();
    expect(await screen.findByText(/abstract and findings report different participant totals/i)).toBeInTheDocument();
    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review evidence" })).toHaveAttribute("href", "/flags/7");
  });

  it("keeps a large integrity record reviewable with filters and bounded expansion", async () => {
    const flags = Array.from({ length: 12 }, (_, index): FlagSummaryOut => ({
      ...FLAG,
      id: index + 1,
      evidence_excerpt: `Possible inconsistency ${index + 1}.`,
      severity: index % 2 === 0 ? "high" : "med",
      problem_kind: `distinct_test_finding_${index + 1}`,
    }));
    stubReport(BASE_REPORT, [], flags);
    // BUG-167: pinned to `flags_view=open` -- this test is about
    // filter/pagination mechanics generally, not the new default-view
    // selection (which would otherwise default to "high" here, since
    // these flags include unresolved high-severity ones).
    renderWithProviders(<SignalReportPage />, {
      route: "/report/5?flags_view=open",
      path: "/report/:checkRunId",
    });

    expect(await screen.findByText("Showing 8 of 12 open findings across 12 locations.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(8);
    fireEvent.click(screen.getByRole("button", { name: "Show 4 more" }));
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(12);

    fireEvent.click(screen.getByRole("button", { name: "High: 6 findings" }));
    expect(screen.getByText("Showing 6 of 6 high findings across 6 locations.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "High: 6 findings" })).toHaveAttribute("aria-pressed", "true");
  });

  it("clusters routed reuse locations by explicit finding identity and preserves every evidence link", async () => {
    const flags = Array.from({ length: 12 }, (_, index): FlagSummaryOut => ({
      ...FLAG,
      id: index + 1,
      check_kind: "originality_reuse",
      severity: "high",
      problem_kind: "reuse_exact_duplicate_passage",
      matched_ref: 34,
      is_passage_level: true,
      evidence_excerpt: `Distinct manuscript passage ${index + 1}.`,
      page_anchor: `p. ${Math.floor(index / 2) + 1}`,
    }));
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("1 open finding")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 1 open finding across 12 locations.")).toBeInTheDocument();
    expect(screen.getByText("Possible match with archived manuscript #34")).toBeInTheDocument();
    // BUG-169: a multi-location cluster now surfaces ONE evidence link
    // pre-expansion (the summary link -- both it and every per-location
    // link share the visible text "Review evidence", so `aria-label`
    // supplies the actual accessible name, which is how every one of
    // these queries distinguishes them). Deliberately says "one of N",
    // never "first": `flags[0]` isn't a stable ordinal (the API re-sorts
    // unresolved before overridden, so array position moves as flags get
    // resolved) -- ux-critic live-reproduced that claiming "first" would
    // assert a fact the system can't back up.
    expect(screen.getByRole("link", { name: "Review evidence at p. 1 (one of 12 locations)" })).toHaveAttribute("href", "/flags/1");
    expect(screen.queryByRole("link", { name: /, location \d+ of 12/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show 12 locations" }));
    expect(screen.getByRole("button", { name: "Hide 12 locations" })).toHaveAttribute("aria-expanded", "true");
    // The template `, location {i} of {N}` is specific to the per-location
    // links -- the summary link's own distinct "(one of N locations)"
    // phrasing does not match it, so this count stays exactly 12.
    const locationLinks = screen.getAllByRole("link", { name: /, location \d+ of 12/ });
    expect(locationLinks).toHaveLength(12);
    expect(new Set(locationLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(12);
    expect(screen.getByRole("link", { name: "Review evidence at p. 1, location 2 of 12" })).toHaveAttribute("href", "/flags/2");
    // The pre-expansion summary link is still present and un-duplicated
    // by the per-location list -- distinct aria-label, same href.
    expect(screen.getByRole("link", { name: "Review evidence at p. 1 (one of 12 locations)" })).toHaveAttribute("href", "/flags/1");
    expect(screen.getByText(/open and resolved locations, so those two filter counts may overlap/i)).toBeInTheDocument();
    expect(screen.getByText(/Distinct manuscript passage 12\./)).toBeInTheDocument();
  });

  it("restores the active finding view and expanded cluster from report URL state", async () => {
    const flags = Array.from({ length: 3 }, (_, index): FlagSummaryOut => ({
      ...FLAG,
      id: index + 1,
      check_kind: "originality_reuse",
      severity: "high",
      problem_kind: "reuse_exact_duplicate_passage",
      matched_ref: 34,
      is_passage_level: true,
      evidence_excerpt: `Passage ${index + 1}`,
      page_anchor: `p. ${index + 1}`,
    }));
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, {
      route: "/report/5?flags_view=high&flags_clusters_open=1",
      path: "/report/:checkRunId",
    });

    expect(await screen.findByRole("button", { name: "High: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Hide 3 locations" })).toHaveAttribute("aria-expanded", "true");
    // 3 per-location links (expanded), distinguished from the always-
    // present summary link (BUG-169) by the per-location-only ", location
    // N of" phrasing.
    expect(screen.getAllByRole("link", { name: /, location \d+ of 3/ })).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Review evidence at p. 1 (one of 3 locations)" })).toBeInTheDocument();
  });

  it("BUG-167: restores how many findings were revealed ('Show N more') from report URL state, not just the filter", async () => {
    // Reproduces the ux-critic-confirmed defect: returning from a flag's
    // own detail page used to always reset "Show N more" back to its
    // default, discarding however many times the instructor had already
    // expanded the list -- on a report large enough that the target flag
    // lived behind that expansion, the return control could vanish
    // entirely. Persisting this the same way `flags_view`/
    // `flags_clusters_open` already are means a genuine remount (leaving
    // `/report/:id` for `/flags/:id` and back IS one) restores it.
    const flags = Array.from({ length: 12 }, (_, index): FlagSummaryOut => ({
      ...FLAG,
      id: index + 1,
      evidence_excerpt: `Possible inconsistency ${index + 1}.`,
      severity: index % 2 === 0 ? "high" : "med",
      problem_kind: `distinct_test_finding_${index + 1}`,
    }));
    stubReport(BASE_REPORT, [], flags);
    // `flags_view=open` pinned for the same reason as the test above --
    // this test is about `flags_visible` persistence, not the new
    // default-view selection.
    renderWithProviders(<SignalReportPage />, {
      route: "/report/5?flags_view=open&flags_visible=12",
      path: "/report/:checkRunId",
    });

    expect(await screen.findByText("Showing 12 of 12 open findings across 12 locations.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(12);
    expect(screen.queryByRole("button", { name: /Show \d+ more/ })).not.toBeInTheDocument();
  });

  // BUG-167: the default FILTER (not the underlying declaration order,
  // which stays fixed per BUG-033) is now the worst unresolved severity
  // present, so what forces the verdict doesn't require scrolling past
  // every lower-severity finding to reach.
  it("defaults the flags filter to High when the report has unresolved high-severity signals", async () => {
    const flags = [
      { ...FLAG, id: 1, severity: "low" as const },
      { ...FLAG, id: 2, severity: "high" as const },
    ];
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("button", { name: "High: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/High severity is shown first/)).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 1 high finding across 1 location.")).toBeInTheDocument();
  });

  it("defaults to Medium when no unresolved high-severity signals remain but medium ones do", async () => {
    const flags = [
      { ...FLAG, id: 1, severity: "low" as const },
      { ...FLAG, id: 2, severity: "med" as const },
    ];
    // unresolved_high_flag_count is the backend's own authoritative
    // aggregate (report/scoring.py's own gate) -- deliberately trusted
    // over recomputing from the raw flags array client-side.
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("button", { name: "Medium: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Medium severity is shown first/)).toBeInTheDocument();
  });

  it("defaults to Open (not High/Medium) when neither an unresolved high nor medium signal exists", async () => {
    const flags = [{ ...FLAG, id: 1, severity: "low" as const }];
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("button", { name: "Open: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/severity is shown first/)).not.toBeInTheDocument();
  });

  it("BUG-167 (ui-designer-found live bug): the hero's high-severity KPI link jumps to and correctly filters the signals list, not a stale pressed state", async () => {
    // Reproduces exactly what ui-designer found broken before the `view`/
    // `visibleCount` useState-mirror fix: a URL change from something
    // OTHER than the filter buttons themselves (here, the hero link)
    // used to leave the pressed filter button showing the PREVIOUS view.
    const flags = [
      { ...FLAG, id: 1, severity: "low" as const },
      { ...FLAG, id: 2, severity: "high" as const },
    ];
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], flags);
    renderWithProviders(<SignalReportPage />, {
      route: "/report/5?flags_view=low",
      path: "/report/:checkRunId",
    });

    expect(await screen.findByRole("button", { name: "Low: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    const kpiLink = screen.getByRole("link", { name: /Open high-severity signals: 1\./ });
    expect(kpiLink).toHaveAttribute("href", expect.stringContaining("flags_view=high"));
    fireEvent.click(kpiLink);

    expect(await screen.findByRole("button", { name: "High: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Low: 1 finding" })).toHaveAttribute("aria-pressed", "false");
  });

  it("BUG-167 (ux-critic finding, live-reproduced): the KPI link actually scrolls its target into view, unlike a plain hash-only jump link", async () => {
    // jsdom has no real layout engine, so `window.scrollY` never reflects
    // anything meaningful here -- this asserts the FIX's actual mechanism
    // (an explicit `scrollIntoView` call) fires for the KPI link, which a
    // react-router `Link` needs because changing the search string means
    // react-router intercepts the click and no native browser anchor-
    // scroll ever happens for it (unlike the plain `<a href="#...">`
    // jump-nav links, which get that scroll for free and must NOT get a
    // second, redundant one -- BUG-158's own reasoning, preserved here).
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], [{ ...FLAG, severity: "high" }]);
      renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

      fireEvent.click(await screen.findByRole("link", { name: /Inspect signals/ }));
      expect(scrollIntoView).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("link", { name: /Open high-severity signals: 1\./ }));
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("renders the high-severity KPI as plain text, not a link, when there are none to jump to", async () => {
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], []);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByRole("heading", { name: "Readiness report" });
    expect(screen.queryByRole("link", { name: /Open high-severity signals/ })).not.toBeInTheDocument();
  });

  const ME_INSTRUCTOR = { id: 9, email: "me@tip.edu.ph", display_name: "Me", onboarding_dismissed_at: null };

  it("BUG-167: marks a flag Viewed on the card after its evidence has actually been opened", async () => {
    window.localStorage.clear();
    window.localStorage.setItem("veridical.flags-viewed.v1.9", JSON.stringify([7]));
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/report": BASE_REPORT,
      "/check-runs/5/escalated": [],
      // Distinct problem_kind/evidence -- otherwise `clusterFlagFindings`
      // (correctly) merges two near-identical flags into ONE multi-
      // location finding, which renders the "Viewed N of M locations"
      // summary variant instead of the plain single-flag "Viewed" badge
      // this test means to check.
      "/check-runs/5/flags": [
        FLAG,
        { ...FLAG, id: 8, problem_kind: "unrelated_finding", evidence_excerpt: "A different excerpt entirely." },
      ],
      "/check-runs/5/share": null,
      "/auth/me": ME_INSTRUCTOR,
    }));
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const viewedBadges = await screen.findAllByText("Viewed");
    expect(viewedBadges).toHaveLength(1);
    expect(screen.getByText("You have reviewed evidence for 1 of 2 open findings.")).toBeInTheDocument();
    window.localStorage.clear();
  });

  it("shows no per-card Viewed badge, but an honest 0-of-N coverage line, when nothing has been opened yet", async () => {
    // The coverage line is shown whenever an open finding exists at all,
    // even at 0 reviewed -- "0 of 1" is itself real, honest information
    // (ground rule 9), not noise to suppress until something's non-zero.
    window.localStorage.clear();
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/report": BASE_REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [FLAG],
      "/check-runs/5/share": null,
      "/auth/me": ME_INSTRUCTOR,
    }));
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Showing 1 of 1 open finding across 1 location.");
    expect(screen.queryByText("Viewed")).not.toBeInTheDocument();
    expect(screen.getByText("You have reviewed evidence for 0 of 1 open findings.")).toBeInTheDocument();
  });

  it("keeps near-identical citation evidence separate when problem kinds differ", async () => {
    const sharedExcerpt = "Sultan et al. (2026). A source with two distinct review problems.";
    stubReport(BASE_REPORT, [], [
      { ...FLAG, id: 21, check_kind: "citation_integrity", problem_kind: "uncited_reference", evidence_excerpt: sharedExcerpt, page_anchor: "reference list" },
      { ...FLAG, id: 22, check_kind: "citation_integrity", problem_kind: "unverifiable_not_found", evidence_excerpt: sharedExcerpt, page_anchor: "reference #15" },
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText("Reference may not be cited in the manuscript body")).toBeInTheDocument();
    expect(screen.getByText("Source not found in the databases checked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /locations/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(2);
  });

  it("BUG-168: states the problem, in the product's own vocabulary, on the card heading -- not just the student's excerpt", async () => {
    // Confirms end-to-end, on a real rendered card, that a kind
    // `problemLabel.ts` only just gained (this ticket extended the table
    // from 14 to 26 reachable kinds) actually reaches the card -- a unit
    // test on `problemLabel()` alone can't prove the wiring itself works.
    stubReport(BASE_REPORT, [], [
      {
        ...FLAG,
        id: 30,
        check_kind: "internal_agreement",
        problem_kind: "agreement_contradictory",
        criterion_text: null,
        evidence_excerpt: "The methodology section describes a mixed-methods design.",
      },
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    const heading = await screen.findByRole("heading", { name: "Objective and result may contradict each other" });
    // The excerpt is still shown, but subordinate to (i.e. not replacing)
    // the problem statement -- this is the ticket's own literal ask.
    expect(within(heading.closest("li")!).getByText(/mixed-methods design/)).toBeInTheDocument();
  });

  it("falls back to the criterion text, then an honest generic label, for a kind with no mapped problem statement", async () => {
    stubReport(BASE_REPORT, [], [
      {
        ...FLAG,
        id: 31,
        problem_kind: "not_yet_mapped_future_kind",
        criterion_text: "Statement of the problem is coherent and well-supported",
      },
      {
        ...FLAG,
        id: 32,
        problem_kind: "not_yet_mapped_future_kind",
        criterion_text: null,
      },
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Showing 2 of 2 open findings across 2 locations.");
    expect(screen.getByRole("heading", { name: "Statement of the problem is coherent and well-supported" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Possible inconsistency" })).toBeInTheDocument();
  });

  it("BUG-169: surfaces a Review-evidence link on a multi-location card BEFORE expansion, regardless of severity", async () => {
    // The ticket's own complaint was specifically about high-severity
    // reuse findings, but the fix is scoped to every multi-location
    // cluster -- the asymmetry is a location-count problem, not a
    // severity one (a medium-severity multi-location cluster used to
    // hide its evidence exactly the same way a high-severity one did).
    const flags = [
      { ...FLAG, id: 40, severity: "med" as const, check_kind: "internal_agreement", problem_kind: "agreement_partial", matched_ref: null, page_anchor: "p. 3" },
      { ...FLAG, id: 41, severity: "med" as const, check_kind: "internal_agreement", problem_kind: "agreement_partial", matched_ref: null, page_anchor: "p. 9" },
    ];
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, {
      // Both flags are medium severity, so BUG-167's own default-view
      // selection lands on "med" here, not "open" -- pinned explicitly so
      // this test is about the evidence link, not that other feature.
      route: "/report/5?flags_view=open",
      path: "/report/:checkRunId",
    });

    await screen.findByText("Showing 1 of 1 open finding across 2 locations.");
    expect(
      screen.getByRole("link", { name: "Review evidence at p. 3 (one of 2 locations)" }),
    ).toHaveAttribute("href", "/flags/40");
  });

  it("BUG-169: never labels the summary evidence link 'first' -- flags[0] is just array position, not a stable ordinal", async () => {
    // ux-critic live-reproduced that the API re-sorts unresolved before
    // overridden, so which flag sits at index 0 moves as siblings get
    // resolved -- an instructor could reasonably read "first" as "the one
    // I already checked" when the underlying order has changed under
    // them. Regression: whichever flag the API hands back as index 0, the
    // summary link must describe it as "one of N", never "first of N".
    const flags = [
      { ...FLAG, id: 41, severity: "med" as const, check_kind: "internal_agreement", problem_kind: "agreement_partial", matched_ref: null, page_anchor: "p. 9" },
      { ...FLAG, id: 40, severity: "med" as const, check_kind: "internal_agreement", problem_kind: "agreement_partial", matched_ref: null, page_anchor: "p. 3" },
    ];
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?flags_view=open", path: "/report/:checkRunId" });

    await screen.findByText("Showing 1 of 1 open finding across 2 locations.");
    expect(
      screen.getByRole("link", { name: "Review evidence at p. 9 (one of 2 locations)" }),
    ).toHaveAttribute("href", "/flags/41");
    expect(screen.queryByRole("link", { name: /first of/ })).not.toBeInTheDocument();
  });

  it("BUG-169: never adds the summary evidence link to a single-location card (already had one) or the public share view (no actions at all)", async () => {
    stubReport(BASE_REPORT, [], [FLAG]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Showing 1 of 1 open finding across 1 location.");
    // Exactly the pre-existing single-location link, no duplicate summary
    // control alongside it.
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(1);
    expect(screen.queryByRole("link", { name: /first of/ })).not.toBeInTheDocument();
  });

  it("migrates legacy reuse percentages without rewriting quoted manuscript text", async () => {
    const legacyWholeDocument: FlagSummaryOut = {
      ...FLAG,
      id: 8,
      check_kind: "originality_reuse",
      problem_kind: "reuse_exact_duplicate",
      evidence_excerpt: "This manuscript appears to be a duplicate or near-duplicate (100.0% match) of archived manuscript #34.",
      page_anchor: "whole document",
    };
    const quotedPassage: FlagSummaryOut = {
      ...legacyWholeDocument,
      id: 9,
      problem_kind: "reuse_exact_duplicate_passage",
      is_passage_level: true,
      evidence_excerpt: "The recorded process completed 100% of the planned cases.",
      page_anchor: "p. 18",
    };
    stubReport(BASE_REPORT, [], [legacyWholeDocument, quotedPassage]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByText(/duplicate or near-duplicate of archived manuscript #34/i)).toBeInTheDocument();
    expect(screen.queryByText(/100\.0% match/)).not.toBeInTheDocument();
    expect(screen.getByText(/completed 100% of the planned cases/)).toBeInTheDocument();
  });

  it("requires a reason before an instructor can reject a Ready manuscript", async () => {
    stubReport();
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Reject manuscript" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this manuscript?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject manuscript" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Enter a reason before confirming.");
  });

  it("opens a transparent, read-only sharing flow without exposing a score", async () => {
    stubReport();
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Share report" }));
    const dialog = screen.getByRole("dialog", { name: "Share this report" });
    expect(within(dialog).getByText("Treat this link as semi-confidential")).toBeInTheDocument();
    expect(
      await within(dialog).findByRole("button", { name: "Create read-only link" }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("92%")).not.toBeInTheDocument();
  });

  it("survives a rolling deploy response that omits integrity status", async () => {
    const { integrity_check_status: _omitted, ...staleReport } = BASE_REPORT;
    stubReport(staleReport as ReportOut);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("heading", { name: "Final defense-readiness decision" })).toBeInTheDocument();
  });
});
