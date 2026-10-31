import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscalatedItemOut, FlagSummaryOut, ReportOut } from "../api/types";
import { renderWithReviewProviders as renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalReportPage } from "./SignalReport";
import { pendingRouteReturnElement, rememberRouteReturnFocus } from "../routing/useRouteFocus";
import { useLocation } from "react-router";
import { SignalDocumentViewerPage } from "../document/SignalDocumentViewer";

vi.mock("../document/PdfPane", () => ({ PdfPane: () => <div>PDF reader outside this report-flow control</div> }));

function ReviewRouteFamily() {
  const location = useLocation();
  return location.pathname.endsWith("/document") ? <SignalDocumentViewerPage /> : <SignalReportPage />;
}

async function enterSelectedCriterion(fromList = false) {
  if (!fromList) {
    await screen.findByRole("heading", { name: ESCALATED.criterion_text, level: 3 });
    return;
  }
  const link = await screen.findByRole("link", { name: `Review criterion: ${ESCALATED.criterion_text}` });
  link.focus(); fireEvent.click(link);
  await screen.findByRole("heading", { name: ESCALATED.criterion_text, level: 3 });
}

// Criteria are graded only in manuscript review; the readiness report is
// the read-only record.
async function renderCriterionFromReport() {
  renderWithProviders(<ReviewRouteFamily />, { route: "/report/5/document?view=decision&step=criteria&pane=review", path: "/report/:checkRunId/*" });
  await enterSelectedCriterion(true);
}

const BASE_REPORT: ReportOut = {
  assessment_revision: 0,
  check_run_id: 5,
  manuscript_id: 5,
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
    "/check-runs/5/document": { manuscript_id: 5, source_format: "pdf", available: false, original_filename: "source.pdf", purged_at: null, unavailable_reason: "No source fixture in this report-flow control", regions: [] },
    "/check-runs/5/document/reuse-matches": { matches: [], passage_archive_size_n: 0 },
  }));
}

describe("SignalReportPage", () => {
  it("reads the report and its findings once per page load", async () => {
    stubReport();
    const answer = globalThis.fetch as unknown as (...args: unknown[]) => Promise<Response>;
    const reads: Record<string, number> = {};
    vi.stubGlobal("fetch", vi.fn(async (...args: unknown[]) => {
      const path = new URL(String(args[0]), "http://localhost").pathname;
      reads[path] = (reads[path] ?? 0) + 1;
      return answer(...args);
    }));
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });
    await screen.findByRole("heading", { name: "Final defense-readiness decision" });
    // Let every panel that mounts after the first answer settle.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    expect(reads["/check-runs/5/report"]).toBe(1);
    expect(reads["/check-runs/5/flags"]).toBe(1);
    expect(reads["/check-runs/5/escalated"]).toBe(1);
    expect(reads["/check-runs/5/share"]).toBe(1);
  });

  it.each([
    [false, "1 open finding · 1 source-age note"],
    [true, "2 open findings"],
  ])("counts source-age notes apart from findings unless the format counts them (counted=%s)", async (counted, label) => {
    const recency = (id: number, excerpt: string): FlagSummaryOut => ({ ...FLAG, id, check_kind: "citation_integrity", severity: "low",
      problem_kind: "outside_recency_window", evidence_excerpt: excerpt, page_anchor: `reference #${id}`,
      affects_readiness: counted, readiness_exclusion_reason: counted ? null : "recency_note_not_counted" });
    stubReport(BASE_REPORT, [], [
      { ...FLAG, id: 8, check_kind: "citation_integrity", severity: "low", problem_kind: "orphan_in_text_citation", evidence_excerpt: "Cruz, 2020" },
      recency(9, "Biggs, J. (1996). Enhancing teaching."), recency(10, "Tang, C. (2011). Teaching for quality."),
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?section=findings&module=citation_integrity", path: "/report/:checkRunId" });
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("records the decision on the report and offers grade changes beside the records", async () => {
    stubReport({ ...BASE_REPORT, results: [{ ...BASE_REPORT.results[0],
      resolution: { type: "mark_pass", reason: "Checked against the manuscript.", ai_majority_verdict: null } }] });
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });
    expect(await screen.findByRole("heading", { name: "Final defense-readiness decision" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve for defense" })).toBeInTheDocument();
    const records = screen.getByRole("region", { name: "Criterion records" });
    expect(within(records).queryByRole("link", { name: "Change grades in manuscript review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Report review order" })).not.toBeInTheDocument();
  });

  it.each([
    ["completed", "Completion recorded.", false],
    ["completed", "Completion recorded.", true],
    ["not_applicable", "Not applicable.", false],
  ] as const)("shows saved statistical scope independently of findings (%s, %s, concern=%s)", async (state, label, hasConcern) => {
    const flag = { ...FLAG, check_kind: "statistical_forensics", problem_kind: "p_value_decision_error", evidence_excerpt: "t(28) = 2.45, p = .500" };
    stubReport({ ...BASE_REPORT, statistical_check_scope: { state }, historical_native_math_unverified: true }, [], hasConcern ? [flag] : []);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=statistical_forensics", path: "/report/:checkRunId" });
    expect(await screen.findByText(`Statistical checks: ${label}`)).toBeVisible();
    if (hasConcern) {
      expect(await screen.findByText("1 open finding")).toBeVisible();
      expect(await screen.findByRole("link", { name: /Review evidence/ })).toBeVisible();
    }
    // Coverage history lives on its own record tab.
    fireEvent.click(screen.getByRole("tab", { name: /^Assessment limits/ }));
    expect(screen.getAllByText("Saved native Word coverage is unverified")[0]).toBeVisible();
  });
  it.each(["review-criteria", "criteria-results-heading", "coverage-statement-heading"])("preserves a workspace section intent while its destination report is slow: %s", async target => {
    const path = "/report/5?criterion_record=all";
    let release!: (value: Response) => void;
    const report = { ...BASE_REPORT, rubric_needs_review: true, rubric_parse_issues: ["Check the retained format."] };
    const fallback = stubFetchByPath({ "/check-runs/5/escalated": [], "/check-runs/5/flags": [], "/check-runs/5/share": null });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).includes("/check-runs/5/report")
      ? new Promise<Response>(resolve => { release = resolve; }) : fallback(input)));
    rememberRouteReturnFocus(path, "/report/5/document", target, { armed: true, fallbackElementId: "readiness-report-heading", scrollBlock: "start" });
    renderWithProviders(<SignalReportPage />, { route: `${path}#${target}`, path: "/report/:checkRunId" });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 650)); });
    expect(pendingRouteReturnElement(path)).toBe(target);
    await act(async () => { release(new Response(JSON.stringify(report), { status: 200 })); });
    await waitFor(() => expect(document.getElementById(target)).toHaveFocus());
  });
  it.each(["section", "criterion", "newer intent", "unavailable"])("preserves an owned slow Audit return without an early fallback: %s", async scenario => {
    const canonical = scenario === "criterion" || scenario === "unavailable";
    const criterion = scenario === "unavailable" ? 99 : 1;
    const target = canonical ? `criterion-record-5-${criterion}` : "criteria-results-heading";
    const path = `/report/5?criterion_record=${canonical ? criterion : "all"}`;
    let release!: (value: Response) => void;
    let held = false;
    const fetcher = stubFetchByPath({ "/check-runs/5/escalated": [], "/check-runs/5/flags": [], "/check-runs/5/share": null });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).includes("/check-runs/5/report")
      ? held ? Promise.resolve(new Response(JSON.stringify(BASE_REPORT), { status: 200 }))
        : new Promise<Response>(resolve => { held = true; release = resolve; }) : fetcher(input)));
    rememberRouteReturnFocus(path, "/audit?check_run_id=5", target, { armed: true, fallbackElementId: "report-audit-5" });
    renderWithProviders(<SignalReportPage />, { route: `${path}#${target}`, path: "/report/:checkRunId" });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 650)); });
    expect(screen.queryByRole("link", { name: "View Audit" })).not.toBeInTheDocument();
    if (scenario === "newer intent") {
      const control = document.createElement("button");
      control.textContent = "New reader task";
      document.body.append(control);
      control.focus();
    }
    await act(async () => { release(new Response(JSON.stringify(BASE_REPORT), { status: 200 })); });
    await screen.findByRole("link", { name: "View Audit" });
    if (scenario === "newer intent") {
      expect(screen.getByRole("button", { name: "New reader task" })).toHaveFocus();
      screen.getByRole("button", { name: "New reader task" }).remove();
    } else if (scenario === "unavailable") {
      await waitFor(() => expect(screen.getByRole("link", { name: "View Audit" })).toHaveFocus(), { timeout: 2000 });
      expect(screen.getByText("That recorded criterion is not available in this report.")).toBeVisible();
    } else await waitFor(() => expect(document.getElementById(target)).toHaveFocus());
  });

  it.each([false, true])("offers one forward way into manuscript review and no progress link for a finished check (decided=%s)", async (decided) => {
    stubReport({ ...BASE_REPORT, ...(decided ? { decision: "approved" as const, decided_at: "2026-10-29T08:00:00Z" } : {}) });
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });
    const forward = await screen.findByRole("link", { name: decided ? "Open manuscript review" : "Continue review" });
    expect(forward).toHaveAttribute("href", "/report/5/document");
    expect(screen.queryByRole("link", { name: decided ? "Continue review" : "Open manuscript review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View check progress" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /manual review/i })).not.toBeInTheDocument();
  });

  it.each(["fake", "real", "unknown"] as const)("BUG-435: report AI disclosure is scoped to responses for mode %s", async (mode) => {
    stubReport({ ...BASE_REPORT, llm_mode: mode });
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });
    await screen.findByRole("link", { name: "Continue review" });
    if (mode === "fake") {
      expect(screen.getByText("Test-mode AI responses")).toBeVisible();
      expect(screen.getByText("These fixture responses are not a real manuscript assessment.")).toBeVisible();
      expect(screen.queryByText(/This report was produced with fixture responses/)).not.toBeInTheDocument();
    } else {
      expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
      if (mode === "unknown") expect(screen.getByText("This record does not establish whether responses came from a real external model or simulated fixtures. Check the recorded evidence directly before relying on AI-derived results.")).toBeVisible();
      else expect(screen.queryByText("AI response mode could not be verified")).not.toBeInTheDocument();
    }
  });
  it("discloses missing support records without inferring their age", async () => {
    stubReport({ ...BASE_REPORT, historical_semantic_support_unverified: true });
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });
    expect(await screen.findByText("AI evidence support needs review"))
      .toBeInTheDocument();
    expect(screen.getByText(/Evidence checks are not confirmed for every criterion/)).toBeVisible();
    expect(screen.queryByText(/suggestions predate criterion-support validation/)).not.toBeInTheDocument();
  });
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

  it("keeps the completed criteria state when the criteria step has no unresolved items", async () => {
    stubReport({ ...BASE_REPORT, pending_review_count: 0 }, []);
    renderWithProviders(<ReviewRouteFamily />, { route: "/report/5/document?view=decision&step=criteria&pane=review", path: "/report/:checkRunId/*" });
    expect(await screen.findByText("No unresolved criteria")).toBeVisible();
    expect(screen.queryByText("Criterion not available")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next criterion" })).not.toBeInTheDocument();
  });

  it.each([
    ["not_applicable", "Not applicable to this manuscript"],
    ["completed", "No findings to review"],
    ["partial", "Check partly completed"],
    ["incomplete", "Check not fully run"],
    ["not_recorded", "No saved check status"],
  ] as const)("shows a neutral empty category matching its saved %s status", async (state, title) => {
    stubReport({ ...BASE_REPORT, integrity_modules: [{ check_kind: "internal_agreement", state }] }, [], []);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });
    expect(await screen.findByRole("heading", { name: title })).toBeVisible();
    const panel = document.getElementById("integrity-signals")!;
    expect(panel.querySelector(".signal-alert--success")).toBeNull();
    expect(panel.querySelector(".signal-integrity-review-body")).toContainElement(screen.getByRole("group", { name: "Integrity check modules" }));
    const notes = within(panel).getByText("How findings affect readiness").closest("details")!;
    expect(notes).not.toHaveAttribute("open");
    fireEvent.click(within(notes).getByText("How findings affect readiness"));
    expect(within(notes).getByText(/an empty list does not establish that the manuscript passed/)).toBeVisible();
  });

  it("keeps a failed category read distinct from an empty result", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/report": BASE_REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/share": null,
      "/check-runs/5/flags": new Response(JSON.stringify({ error: { code: "internal", message: "Read unavailable" } }), { status: 500 }) }));
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings", path: "/report/:checkRunId" });
    expect(await screen.findByText("Other findings could not be loaded.")).toBeVisible();
    expect(document.querySelector(".signal-integrity-empty")).toBeNull();
  });

  it("puts unresolved judgment first, permits reasoned return and blocks approval/rejection", async () => {
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
    renderWithProviders(<ReviewRouteFamily />, { route: "/report/5/document", path: "/report/:checkRunId/*" });

    // With no step in the address, manuscript review opens on the first step
    // that still needs the instructor.
    const firstTask = await screen.findByRole("heading", { name: "Criteria needing your judgment" });
    expect(firstTask).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Step 1: Review criteria/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByText(ESCALATED.criterion_text)).toBeInTheDocument();
    expect(screen.getByText("1 unresolved")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Step 4: Decision summary/ }));
    fireEvent.click(await screen.findByRole("link", { name: "Record the decision on the readiness report" }));
    expect(await screen.findByRole("button", { name: "Approve for defense" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Return for revision" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject manuscript" })).toBeDisabled();
  });

  it("makes a chosen-but-unconfirmed resolution legible as a pending step, not a done one (BUG-145)", async () => {
    stubReport(BASE_REPORT, [ESCALATED]);
    await renderCriterionFromReport();

    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));

    // The chosen resolution is a real heading, not inline text -- and the
    // page states plainly that nothing is saved yet, so the two-step
    // choose-then-confirm flow can't be mistaken for a completed action
    // (the original bug: the options vanishing read exactly like success).
    expect(screen.getByRole("heading", { name: "Mark as meets criterion", level: 4 })).toBeInTheDocument();
    expect(screen.getByText("Unsaved grade. Your choice and comment are kept while you review this report.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save grade" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    // Nothing was submitted by choosing -- the resolve mutation only fires on Confirm.
    expect(screen.getByText("1 unresolved")).toBeInTheDocument();

    // The pending state must be announced to assistive tech even though
    // autoFocus moves keyboard/AT focus straight into the reason textarea
    // on the same render (UX review finding: without this, a screen-reader
    // user never hears "nothing is saved yet" at all). A live region is
    // announced from its CONTENT, not its accessible name (which stays
    // empty with no aria-label), so this checks the role exists and wraps
    // the pending copy, not a computed name.
    const status = screen.getByText("Unsaved grade. Your choice and comment are kept while you review this report.").closest<HTMLElement>('[role="status"]')!;
    expect(within(status).getByText("Unsaved grade. Your choice and comment are kept while you review this report.")).toBeInTheDocument();
    expect(within(status).getByText("Mark as meets criterion")).toBeInTheDocument();
  });

  it("returns focus to the criterion heading on Cancel, not to <body> (BUG-145)", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    stubReport(BASE_REPORT, [ESCALATED]);
    await renderCriterionFromReport();

    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));
    const cancel = screen.getByRole("button", { name: "Cancel" });
    const heading = screen.getByRole("heading", { name: ESCALATED.criterion_text });
    const focusedBeforeRemoval: boolean[] = [];
    const originalFocus = heading.focus;
    vi.spyOn(heading, "focus").mockImplementation((options) => {
      focusedBeforeRemoval.push(cancel.isConnected);
      originalFocus.call(heading, options);
    });
    cancel.focus();
    fireEvent.click(cancel);

    expect(heading).toHaveFocus();
    expect(focusedBeforeRemoval).toEqual([false]);
    expect(screen.getByRole("button", { name: "Meets criterion" })).toBeInTheDocument();
  });

  it("BUG-132: every resolution button is described by the unresolved state and essential reason", async () => {
    // A screen-reader user can tab directly to a button (not just read
    // top-to-bottom); before this fix they heard only "Meets criterion,
    // button" -- none of the context explaining WHY this criterion needs
    // review. The unresolved state and recorded reason are in the described-by set,
    // resolving to real elements containing the real context text.
    stubReport(BASE_REPORT, [ESCALATED]);
    await renderCriterionFromReport();

    const button = await screen.findByRole("button", { name: "Meets criterion" });
    const describedByIds = button.getAttribute("aria-describedby")?.split(" ") ?? [];
    expect(describedByIds.length).toBeGreaterThan(0);
    const describedText = describedByIds
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(describedByIds.every((id) => document.getElementById(id))).toBe(true);
    expect(describedText).toContain("Your judgment is needed");
    expect(describedText).not.toContain("Split vote:");
    expect(describedText).toContain(ESCALATED.reason);

    // Every action retains the essential review descriptions. Exclusion
    // also describes its own consequence before the instructor selects it.
    for (const name of ["Does not meet", "Exclude: needs another document"]) {
      const ids = screen.getByRole("button", { name }).getAttribute("aria-describedby")!.split(/\s+/);
      expect(ids).toEqual(expect.arrayContaining(describedByIds));
      expect(ids.every((id) => document.getElementById(id))).toBe(true);
    }
  });

  it("BUG-170: never offers to accept a majority verdict the server has already declared unrecognized for this criterion's scale", async () => {
    // Both passes genuinely agreed (a real ai_majority_verdict, unlike
    // ESCALATED's own tied/no-majority fixture above) on a verdict this
    // criterion's own scale doesn't define -- accepting it is guaranteed
    // to 409 server-side (see the backend's own resolve_escalation guard),
    // so offering the button at all is the exact "offers to accept a
    // verdict it just told you it cannot recognise" incoherence BUG-170
    // named. `reason` already explains why; the fix is to not ALSO offer
    // a button that can only fail.
    const unrecognized: EscalatedItemOut = {
      ...ESCALATED,
      check_result_id: 45,
      agreement: 1.0,
      votes: ["Good", "Good"],
      ai_majority_verdict: "Good",
      verdict_unrecognized: true,
      reason: "The grading response used an unrecognized verdict ('Good') for this criterion's own scale.",
    };
    stubReport(BASE_REPORT, [unrecognized]);
    await renderCriterionFromReport();

    await screen.findByRole("heading", { name: "Criteria needing your judgment" });
    expect(screen.getByText(unrecognized.reason!)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept AI suggestion/ })).not.toBeInTheDocument();
    // The rest of the resolution vocabulary is unaffected -- the guard
    // removes only the one guaranteed-to-fail button.
    expect(screen.getByRole("button", { name: "Meets criterion" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Does not meet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exclude: needs another document" })).toBeInTheDocument();
  });

  it("still offers to accept a real, matchable majority verdict (BUG-170 guard is scoped to verdict_unrecognized, not every majority)", async () => {
    const recognized: EscalatedItemOut = {
      ...ESCALATED,
      check_result_id: 46,
      agreement: 0.667,
      votes: ["pass", "fail", "pass"],
      ai_majority_verdict: "pass",
      reason: "Two of three grading passes agreed.",
    };
    stubReport(BASE_REPORT, [recognized]);
    await renderCriterionFromReport();

    await screen.findByRole("heading", { name: "Criteria needing your judgment" });
    expect(screen.getByRole("button", { name: "Accept AI suggestion: pass" })).toBeInTheDocument();
  });

  it("BUG-170: never renders a whole-document reuse flag's system-authored summary sentence as a manuscript quote on the report's own flag card", async () => {
    // UX review found this exact defect already fixed on FlagDetail.tsx
    // and SignalDocumentViewer.tsx still live one screen earlier -- the
    // report's own flag-card list (the first thing an instructor reads)
    // rendered the same system-authored template sentence inside a real
    // <blockquote>, unconditionally, for every check kind including
    // whole-document/chapter reuse.
    const flags = [{
      ...FLAG,
      id: 50,
      check_kind: "originality_reuse",
      problem_kind: "reuse_high_similarity",
      is_passage_level: false,
      evidence_excerpt: "This manuscript shows high textual similarity to archived manuscript #3 in VERIDICAL's shared originality library: possible shared content or reuse. Please verify manually.",
      page_anchor: "whole document",
    }];
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=originality_reuse", path: "/report/:checkRunId" });

    await screen.findByText(/possible shared content or reuse/);
    // Scoped to the flags section specifically -- the report's own
    // criteria-record table renders unrelated blockquotes for ordinary
    // semantic evidence, which is out of this test's scope.
    const signals = screen.getByRole("region", { name: "Integrity signals" });
    expect(signals.querySelector("blockquote")).not.toBeInTheDocument();
  });

  it("BUG-170: still quotes a passage-level reuse flag on the report's own flag card, unaffected by the fix above", async () => {
    const flags = [{
      ...FLAG,
      id: 51,
      check_kind: "originality_reuse",
      problem_kind: "reuse_exact_duplicate_passage",
      is_passage_level: true,
      evidence_excerpt: "The system uses a hybrid rule-based and AI approach.",
      page_anchor: "p. 31",
    }];
    stubReport(BASE_REPORT, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=originality_reuse", path: "/report/:checkRunId" });

    await screen.findByText(/hybrid rule-based and AI approach/);
    const signals = screen.getByRole("region", { name: "Integrity signals" });
    expect(signals.querySelector("blockquote")).toBeInTheDocument();
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

    expect(await screen.findByText("Test-mode AI responses")).toBeInTheDocument();
    expect(await screen.findByText(/abstract and findings report different participant totals/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^Assessment limits/ }));
    expect(screen.getByRole("heading", { name: "Assessment limits" })).toBeInTheDocument();
    expect(screen.getByText("Required format review")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Assessment limits" })).getByText("Citation integrity")).toBeInTheDocument();
    expect(screen.getByText(/2 items skipped because a service was unavailable/)).toBeInTheDocument();
    const rerunLink = screen.getByRole("link", { name: "Run again: citation integrity check for this manuscript" });
    expect(rerunLink).toHaveAttribute("href", "/dashboard?rerun=5");
    // BUG-127 (UX review): WCAG 2.5.3 Label in Name requires the
    // accessible name to CONTAIN the visible text -- a link whose
    // aria-label doesn't start with its own visible label fails a
    // voice-control user trying to say "click Run again".
    expect(rerunLink.textContent?.trim()).toBe("Run again");
    expect(rerunLink.getAttribute("aria-label")?.toLowerCase().startsWith("run again")).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: /^Integrity findings/ }));
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
      route: "/report/5?review_step=findings&module=internal_agreement&flags_view=open",
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=originality_reuse", path: "/report/:checkRunId" });

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
    // resolved) -- UX review live-reproduced that claiming "first" would
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
      route: "/report/5?review_step=findings&module=originality_reuse&flags_view=high&flags_clusters_open=1",
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
    // Reproduces the UX review-confirmed defect: returning from a flag's
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
      route: "/report/5?review_step=findings&module=internal_agreement&flags_view=open&flags_visible=12",
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

    expect(await screen.findByRole("button", { name: "Medium: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Medium severity is shown first/)).toBeInTheDocument();
  });

  it("defaults to Open (not High/Medium) when neither an unresolved high nor medium signal exists", async () => {
    const flags = [{ ...FLAG, id: 1, severity: "low" as const }];
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

    expect(await screen.findByRole("button", { name: "Open: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/severity is shown first/)).not.toBeInTheDocument();
  });

  it("BUG-167 (design review-found live bug): the hero's high-severity KPI link jumps to and correctly filters the signals list, not a stale pressed state", async () => {
    // Reproduces exactly what design review found broken before the `view`/
    // `visibleCount` useState-mirror fix: a URL change from something
    // OTHER than the filter buttons themselves (here, the hero link)
    // used to leave the pressed filter button showing the PREVIOUS view.
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const flags = [
      { ...FLAG, id: 1, severity: "low" as const },
      { ...FLAG, id: 2, severity: "high" as const },
    ];
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], flags);
    renderWithProviders(<SignalReportPage />, {
      route: "/report/5?review_step=findings&module=internal_agreement&flags_view=low",
      path: "/report/:checkRunId",
    });

    expect(await screen.findByRole("button", { name: "Low: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    const kpiLink = screen.getByRole("link", { name: /Open high-severity findings counted for readiness: 1\./ });
    expect(kpiLink).toHaveAttribute("href", expect.stringContaining("flags_view=high"));
    kpiLink.focus(); fireEvent.click(kpiLink);

    expect(await screen.findByRole("button", { name: "High: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Low: 1 finding" })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(screen.getByRole("region", { name: "Integrity signals" })).toHaveFocus());
  });

  it("BUG-167 (UX review finding, live-reproduced): the KPI link actually scrolls its target into view, unlike a plain hash-only jump link", async () => {
    // jsdom has no real layout engine, so `window.scrollY` never reflects
    // anything meaningful here -- this asserts the FIX's actual mechanism
    // (an explicit `scrollIntoView` call) fires for the KPI link, which a
    // react-router `Link` needs because changing the search string means
    // react-router intercepts the click and no native browser anchor-
    // scroll ever happens for it (unlike the plain `<a href="#...">`
    // jump-nav links, which get that scroll for free and must NOT get a
    // second, redundant one -- BUG-158's own reasoning, preserved here).
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], [{ ...FLAG, severity: "high" }]);
      renderWithProviders(<SignalReportPage />, { route: "/report/5?module=internal_agreement", path: "/report/:checkRunId" });

      const kpi = await screen.findByRole("link", { name: /Open high-severity findings counted for readiness: 1\./ });
      kpi.focus(); fireEvent.click(kpi);
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("renders the high-severity KPI as plain text, not a link, when there are none to jump to", async () => {
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], []);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByRole("heading", { name: "Readiness report" });
    expect(screen.queryByRole("link", { name: /Open high-severity findings counted for readiness/ })).not.toBeInTheDocument();
  });

  it("distinguishes readiness-counted High findings from High non-scoring leads", async () => {
    const flags: FlagSummaryOut[] = [
      { ...FLAG, id: 1, problem_kind: "agreement_contradictory", affects_readiness: false },
      { ...FLAG, id: 2, check_kind: "originality_reuse", problem_kind: "reuse_exact_duplicate_passage", is_passage_level: true, affects_readiness: false },
      { ...FLAG, id: 3, check_kind: "statistical_forensics", problem_kind: "p_value_decision_error", affects_readiness: true },
    ];
    stubReport({ ...BASE_REPORT, status: "needs_review", unresolved_high_flag_count: 1 }, [], flags);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

    expect(await screen.findByText("Review lead: not scored")).toBeVisible();
    expect(screen.getByRole("link", { name: /Open high-severity findings counted for readiness: 1\./ })).toHaveTextContent("1");
    expect(screen.getAllByText("Needs Review").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /^Review evidence for Comparison lead/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Statistical checks/ }));
    expect(await screen.findByRole("button", { name: "High: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("High severity", { exact: true })).toBeVisible();
    expect(screen.getByText(/Filters use recorded severity, including review leads/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Originality and reuse/ }));
    expect(await screen.findByRole("link", { name: /^Review evidence for Comparison lead 1 of 1\. Record #2\./ })).toBeVisible();
    expect(screen.getByText("L1")).toBeVisible();
    expect(screen.getByText("Record #2")).toBeVisible();
expect(screen.getByText("Not scored. Does not affect readiness or block your decision.")).toBeVisible();
    expect(screen.queryByText("High severity", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open high-severity findings counted for readiness: 1\./ })).toHaveTextContent("1");
    expect(screen.queryByText(/makes a report Not Ready by itself/)).not.toBeInTheDocument();
  });

  it.each([true, undefined])("retains recorded severity unless non-scoring is explicit (%s)", async (affects_readiness) => {
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 1 }, [], [{ ...FLAG, affects_readiness }]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });
    expect(await screen.findByText("High severity", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("Review lead: not scored")).not.toBeInTheDocument();
  });

  it("keeps Medium help truthful when a High non-scoring lead remains", async () => {
    stubReport({ ...BASE_REPORT, unresolved_high_flag_count: 0 }, [], [
      { ...FLAG, id: 1, affects_readiness: false },
      { ...FLAG, id: 2, severity: "med", check_kind: "citation_integrity", problem_kind: "orphan_citation", affects_readiness: true },
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=citation_integrity", path: "/report/:checkRunId" });
    expect(await screen.findByRole("button", { name: "Medium: 1 finding" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Medium severity is shown first\. Filters use recorded severity/)).toBeInTheDocument();
    expect(screen.queryByText(/no unresolved high-severity signals remain/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Internal agreement/ }));
    fireEvent.click(await screen.findByRole("button", { name: "High: 1 finding" }));
    expect(await screen.findByText("Review lead: not scored")).toBeInTheDocument();
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId", reviewInstructorId: ME_INSTRUCTOR.id });

    const viewedBadges = await screen.findAllByText("Opened");
    expect(viewedBadges).toHaveLength(1);
    expect(screen.getByText("You have opened 1 of 2 open findings.")).toBeInTheDocument();
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId", reviewInstructorId: ME_INSTRUCTOR.id });

    await screen.findByText("Showing 1 of 1 open finding across 1 location.");
    expect(screen.queryByText("Opened")).not.toBeInTheDocument();
    expect(screen.getByText("You have opened 0 of 1 open findings.")).toBeInTheDocument();
  });

  it("keeps near-identical citation evidence separate when problem kinds differ", async () => {
    const sharedExcerpt = "Sultan et al. (2026). A source with two distinct review problems.";
    stubReport(BASE_REPORT, [], [
      { ...FLAG, id: 21, check_kind: "citation_integrity", problem_kind: "uncited_reference", evidence_excerpt: sharedExcerpt, page_anchor: "reference list" },
      { ...FLAG, id: 22, check_kind: "citation_integrity", problem_kind: "unverifiable_not_found", evidence_excerpt: sharedExcerpt, page_anchor: "reference #15" },
    ]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=citation_integrity", path: "/report/:checkRunId" });

    expect(await screen.findByText("Reference may not be cited in the manuscript body")).toBeInTheDocument();
    expect(screen.getByText("Source not found in the databases checked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /locations/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Review evidence" })).toHaveLength(2);
  });

  it("BUG-065: each criterion's assessment disclosure has a distinguishing accessible name", async () => {
    // The visible text is identical on every row by design -- a
    // screen-reader links/buttons list must still be able to tell them
    // apart. Two rows here, same visible summary text, different
    // criterion text, asserting the accessible NAME (not the visible
    // text) actually differs.
    const report: ReportOut = {
      ...BASE_REPORT,
      results: [
        { ...BASE_REPORT.results[0], criterion_id: 1, text: "Chapter 1 states the research problem" },
        { ...BASE_REPORT.results[0], criterion_id: 2, text: "Findings answer the stated research questions" },
      ],
    };
    stubReport(report);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    await screen.findByText("Chapter 1 states the research problem");
    // jsdom doesn't compute an implicit ARIA role for a bare <summary>
    // (Chromium does: "button"), so query it directly rather than via
    // getByRole -- the aria-label itself, which real assistive tech
    // reads, is what this regression test actually needs to prove.
    const summaries = Array.from(document.querySelectorAll(".signal-result-card summary"));
    expect(summaries.map((s) => s.getAttribute("aria-label"))).toEqual([
      "AI assessment details: source coverage not recorded: Chapter 1 states the research problem",
      "AI assessment details: source coverage not recorded: Findings answer the stated research questions",
    ]);
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

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
      route: "/report/5?review_step=findings&module=internal_agreement&flags_view=open",
      path: "/report/:checkRunId",
    });

    await screen.findByText("Showing 1 of 1 open finding across 2 locations.");
    expect(
      screen.getByRole("link", { name: "Review evidence at p. 3 (one of 2 locations)" }),
    ).toHaveAttribute("href", "/flags/40");
  });

  it("BUG-169: never labels the summary evidence link 'first' -- flags[0] is just array position, not a stable ordinal", async () => {
    // UX review live-reproduced that the API re-sorts unresolved before
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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement&flags_view=open", path: "/report/:checkRunId" });

    await screen.findByText("Showing 1 of 1 open finding across 2 locations.");
    expect(
      screen.getByRole("link", { name: "Review evidence at p. 9 (one of 2 locations)" }),
    ).toHaveAttribute("href", "/flags/41");
    expect(screen.queryByRole("link", { name: /first of/ })).not.toBeInTheDocument();
  });

  it("BUG-169: never adds the summary evidence link to a single-location card (already had one) or the public share view (no actions at all)", async () => {
    stubReport(BASE_REPORT, [], [FLAG]);
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=internal_agreement", path: "/report/:checkRunId" });

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
    renderWithProviders(<SignalReportPage />, { route: "/report/5?review_step=findings&module=originality_reuse", path: "/report/:checkRunId" });

    expect(await screen.findByText(/duplicate or near-duplicate of archived manuscript #34/i)).toBeInTheDocument();
    expect(screen.queryByText(/100\.0% match/)).not.toBeInTheDocument();
    expect(screen.getByText(/completed 100% of the planned cases/)).toBeInTheDocument();
  });

  it("requires a reason before an instructor can reject a Ready manuscript", async () => {
    stubReport();
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Reject manuscript" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this manuscript?" });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Reject manuscript" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Reject manuscript" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Enter a reason before confirming.");
  });

  it("opens a transparent, read-only sharing flow without exposing a score", async () => {
    stubReport();
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    fireEvent.click(await screen.findByRole("button", { name: "Share report" }));
    const dialog = screen.getByRole("dialog", { name: "Share this report" });
    expect(within(dialog).getByText("Shared links require no sign-in and can be forwarded. They are read-only and do not give access to your Review Desk, Audit, settings or other reports.")).toBeInTheDocument();
    expect(
      await within(dialog).findByRole("button", { name: "Create read-only link" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("No active link.")).toBeVisible();
    expect(within(dialog).queryByText("92%")).not.toBeInTheDocument();
  });

  it("survives a rolling deploy response that omits integrity status", async () => {
    const { integrity_check_status: _omitted, ...staleReport } = BASE_REPORT;
    stubReport(staleReport as ReportOut);
    renderWithProviders(<SignalReportPage />, { route: "/report/5", path: "/report/:checkRunId" });

    expect(await screen.findByRole("heading", { name: "Final defense-readiness decision" })).toBeInTheDocument();
  });
});

// Exercise the real route, including its report-read gate and mounted dialogs.
// Criteria are graded in manuscript review; the decision is recorded on the report.
function readFailurePage(initial: ReportOut = BASE_REPORT, escalated: EscalatedItemOut[] = [], entry: "decision" | "criteria" | "report" = "decision") {
  let current = initial;
  let failure = 0;
  let hold = false;
  let release: (() => void) | undefined;
  let client: QueryClient;
  let postCount = 0;
  function QueryAccess() { client = useQueryClient(); return null; }
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (init?.method === "POST") {
      postCount++;
      if (path.endsWith("/resolve")) {
        const body = JSON.parse(String(init.body));
        const row = { ...BASE_REPORT.results[0], criterion_id: ESCALATED.criterion_id, text: ESCALATED.criterion_text,
          resolution: { type: body.resolution, reason: body.reason, ai_majority_verdict: null } };
        current = { ...current, assessment_revision: current.assessment_revision! + 1, pending_review_count: 0, results: [...BASE_REPORT.results, row] };
        return new Response(JSON.stringify({ check_result_id: ESCALATED.check_result_id, outcome: "passed", score: 100, report: current }));
      }
    }
    if (path.endsWith("/report")) {
      if (hold) await new Promise<void>((resolve) => { release = resolve; });
      return new Response(JSON.stringify(failure ? { error: { code: "unavailable", message: "Controlled read failure" } } : current), { status: failure || 200 });
    }
    return stubFetchByPath({ "/check-runs/5/escalated": current.pending_review_count ? escalated : [],
      "/check-runs/5/share": null, "/check-runs/5/flags": [] })(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const route = entry === "criteria" ? "/report/5/document?view=decision&pane=review" : "/report/5";
  renderWithProviders(<><QueryAccess /><ReviewRouteFamily /></>, { route, path: "/report/:checkRunId/*" });
  return { fail(status = 503) { failure = status; }, fresh(report = current) { failure = 0; current = report; },
    hold() { hold = true; }, release() { hold = false; release?.(); }, posts: () => postCount,
    refresh: () => client.invalidateQueries({ queryKey: ["report", 5], exact: true }) };
}

describe("full report background-read continuity", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("keeps a validated save receipt and exact retrieval available after its report GET fails", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const pending = { ...BASE_REPORT, pending_review_count: 1, results: [...BASE_REPORT.results,
      { ...BASE_REPORT.results[0], criterion_id: 2, text: ESCALATED.criterion_text, outcome: "escalated" as const, score: null }] };
    const c = readFailurePage(pending, [ESCALATED], "criteria");
    await enterSelectedCriterion(true);
    const card = (await screen.findByRole("heading", { name: ESCALATED.criterion_text })).closest("article")!;
    fireEvent.click(within(card).getByRole("button", { name: "Meets criterion" }));
    fireEvent.change(within(card).getByRole("textbox"), { target: { value: "A checkable instructor reason for this criterion." } });
    c.fail(); fireEvent.click(within(card).getByRole("button", { name: "Save grade" }));
    expect(await screen.findByRole("heading", { name: "Judgment saved" })).toBeVisible();
    fireEvent.click(screen.getByRole("link", { name: "Open readiness report" }));
    await screen.findByText("Could not update this report");
    expect(screen.getByText("Last loaded readiness band")).toBeVisible();
    const other = screen.getByRole("link", { name: "View Audit" }); other.focus();
    c.fresh();
    const retry = screen.getAllByRole("button", { name: "Retry report" })[0];
    await waitFor(() => expect(retry).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(retry);
    await waitFor(() => expect(document.getElementById("criterion-record-5-2")).not.toBeNull());
    await waitFor(() => expect(screen.queryByText("Could not update this report")).not.toBeInTheDocument());
    expect(other).toHaveFocus(); expect(c.posts()).toBe(1);
    vi.restoreAllMocks();
  });

  it.each([false, true])("keeps the open dialog, note and focus while read failure blocks confirmation (reopen=%s)", async (reopen) => {
    const initial = reopen ? { ...BASE_REPORT, decision: "approved" as const, decided_at: "2026-10-22T12:00:00Z" } : BASE_REPORT;
    const c = readFailurePage(initial);
    fireEvent.click(await screen.findByRole("button", { name: reopen ? "Reopen this decision" : "Approve for defense" }));
    const dialog = await screen.findByRole("dialog");
    const note = within(dialog).getByRole("textbox");
    fireEvent.change(note, { target: { value: "Preserve this instructor note through the refresh." } }); note.focus();
    c.fail(); await act(async () => { await c.refresh(); });
    await within(dialog).findByText("Reload the report before making or reopening a final decision.");
    expect(note).toHaveFocus(); expect(note).toHaveValue("Preserve this instructor note through the refresh.");
    const confirm = within(dialog).getByRole("button", { name: reopen ? "Reopen decision" : "Approve for defense" });
    expect(confirm).toBeDisabled(); fireEvent.click(confirm); expect(c.posts()).toBe(0);
    c.hold(); c.fresh(); fireEvent.click(within(dialog).getByRole("button", { name: "Retry report" }));
    expect(confirm).toBeDisabled(); fireEvent.click(confirm); expect(c.posts()).toBe(0);
    await act(async () => { c.release(); });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(note).toHaveValue("Preserve this instructor note through the refresh.");
    fireEvent.click(within(dialog).getByRole("button", { name: reopen ? "Keep decision" : "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(c.posts()).toBe(0);
  });

  it("preserves an open note but refuses an obsolete choice after retry reveals another decision", async () => {
    const c = readFailurePage();
    fireEvent.click(await screen.findByRole("button", { name: "Approve for defense" }));
    const dialog = await screen.findByRole("dialog"); const note = within(dialog).getByRole("textbox");
    fireEvent.change(note, { target: { value: "Original note stays available." } }); note.focus();
    c.fail(); await act(async () => { await c.refresh(); });
    await within(dialog).findByText("Reload the report before making or reopening a final decision.");
    c.fresh({ ...BASE_REPORT, assessment_revision: 1, decision: "returned", decided_at: "2026-10-23T00:00:00Z" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry report" }));
    await within(dialog).findByRole("button", { name: "Review current assessment" });
    expect(note).toHaveFocus(); expect(note).toHaveValue("Original note stays available.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve for defense" })); expect(c.posts()).toBe(0);
  });

  it.each([401, 403, 404])("does not retain private report content after access failure %s", async (status) => {
    const c = readFailurePage(BASE_REPORT, [], "report"); await screen.findByRole("link", { name: "Continue review" });
    c.fail(status); await act(async () => { await c.refresh(); });
    expect(await screen.findByText("Could not load this report")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Continue review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Criterion records" })).not.toBeInTheDocument();
    expect(screen.queryByText("Could not update this report")).not.toBeInTheDocument();
  });
});

it("keeps an open reopen reason but refuses a decision that changed during report retry", async () => {
  const c = readFailurePage({ ...BASE_REPORT, decision: "approved", decided_at: "2026-10-22T12:00:00Z" });
  fireEvent.click(await screen.findByRole("button", { name: "Reopen this decision" }));
  const dialog = await screen.findByRole("dialog"); const note = within(dialog).getByRole("textbox");
  fireEvent.change(note, { target: { value: "Reopen reason survives a changed record." } }); note.focus();
  c.fail(); await act(async () => { await c.refresh(); });
  await within(dialog).findByText("Reload the report before making or reopening a final decision.");
  c.fresh({ ...BASE_REPORT, assessment_revision: 1, decision: "returned", decided_at: "2026-10-23T00:00:00Z" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry report" }));
  await within(dialog).findByRole("button", { name: "Review current assessment" });
  expect(note).toHaveFocus(); expect(note).toHaveValue("Reopen reason survives a changed record.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Reopen decision" })); expect(c.posts()).toBe(0);
  vi.unstubAllGlobals();
});


describe("assessment context before instructor judgment", () => {
  afterEach(() => vi.unstubAllGlobals());
  it.each([true, false])("keeps test provenance and conditioned support context before actions (support=%s)", async flagged => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    stubReport({ ...BASE_REPORT, llm_mode: "fake", historical_semantic_support_unverified: flagged }, [ESCALATED]);
    await renderCriterionFromReport();
    const action = await screen.findByRole("button", { name: "Meets criterion" });
    const context = action.closest("article")!.querySelector(".signal-action-assessment-context")!;
    expect(context.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(action).toHaveAccessibleDescription(/Test AI responses. Not a real AI assessment/);
    if (flagged) expect(action).toHaveAccessibleDescription(/AI evidence checks are not all confirmed/);
    else expect(action).not.toHaveAccessibleDescription(/Evidence checks/);
    expect(screen.queryByRole("complementary", { name: "Assessment context" })).not.toBeInTheDocument();
    expect(action.closest("article")!.querySelector(".signal-action-assessment-context")).toBeVisible();
    const ids = action.getAttribute("aria-describedby")!.split(/\s+/);
    expect(ids.every(id => document.getElementById(id))).toBe(true);
    fireEvent.click(action);
    expect(screen.getByRole("button", { name: "Save grade" })).toHaveAccessibleDescription(/Test AI responses. Not a real AI assessment/);
    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus(); fireEvent.click(cancel);
    expect(screen.getByRole("heading", { name: ESCALATED.criterion_text })).toHaveFocus();
  });
});
