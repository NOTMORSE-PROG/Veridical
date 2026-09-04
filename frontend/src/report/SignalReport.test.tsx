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
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

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
    expect(screen.queryByRole("link", { name: /Review evidence at/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show 12 locations" }));
    expect(screen.getByRole("button", { name: "Hide 12 locations" })).toHaveAttribute("aria-expanded", "true");
    const locationLinks = screen.getAllByRole("link", { name: /Review evidence at p\./ });
    expect(locationLinks).toHaveLength(12);
    expect(new Set(locationLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(12);
    expect(screen.getByRole("link", { name: "Review evidence at p. 1, location 2 of 12" })).toHaveAttribute("href", "/flags/2");
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
    expect(screen.getAllByRole("link", { name: /Review evidence at p\./ })).toHaveLength(3);
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
