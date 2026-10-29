import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CriterionResultOut, FlagOut, FlagSummaryOut, ManuscriptViewerOut, ReportOut } from "../api/types";
import { renderWithReviewProviders as renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";
import { CITATION_COMPARISON } from "../test/citationComparisonFixture";
import { localDate } from "../domain/localDate";

vi.mock("./PdfPane", () => ({
  PdfPane: ({ requestedPage, selectedFlagId, fileUrl, regions, onReadText }: { requestedPage: number | null; selectedFlagId: number | null; fileUrl: string; regions: unknown[]; onReadText?: () => void }) => (
    <div data-testid="pdf-pane" data-page={requestedPage ?? ""} data-selection={selectedFlagId ?? ""} data-file={fileUrl} data-regions={regions.length}>PDF source<button onClick={onReadText}>Read text</button></div>
  ),
}));
vi.mock("./DocxPane", () => ({ DocxPane: () => <div>DOCX source</div> }));
vi.mock("./ReuseExplorePanel", () => ({ ReuseExplorePanel: () => <div>Passage exploration</div> }));

const VIEWER: ManuscriptViewerOut = {
  manuscript_id: 10,
  original_filename: "group-syntax.pdf",
  source_format: "pdf",
  available: true,
  unavailable_reason: null,
  purged_at: null,
  page_count: 42,
  regions: [{ flag_id: 7, kind: "page_only", page: 12, end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null }],
};

const SUMMARY: FlagSummaryOut = {
  id: 7,
  check_kind: "internal_agreement",
  severity: "high",
  criterion_text: null,
  evidence_excerpt: "The abstract and findings list different participant totals.",
  page_anchor: "pages 2 and 12",
  overridden: false,
  is_passage_level: false,
  first_upload_context: false,
  confirmed_citation_source: false,
  problem_kind: "numeric_mismatch",
};

const FLAG: FlagOut = {
  assessment_revision: 0,
  id: 7,
  check_result_id: 9,
  check_run_id: 5,
  manuscript_group_label: "Group Syntax",
  check_kind: "internal_agreement",
  criterion_text: null,
  severity: "high",
  confidence: 0.9,
  evidence_excerpt: SUMMARY.evidence_excerpt,
  page_anchor: SUMMARY.page_anchor,
  annotation: null,
  overridden: false,
  override_reason: null,
  ai_verdict_summary: "possible_mismatch",
  ai_reasoning: "The recorded totals differ.",
  llm_mode: "real",
  is_passage_level: false,
  passage_pair: null,
  first_upload_context: false,
  evidence_unavailable: false,
  citation_source_key: null,
  confirmed_citation_source: false,
};

const REPORT: ReportOut = {
  assessment_revision: 0,
  check_run_id: 5,
  manuscript_id: 10,
  manuscript_group_label: "Group Syntax",
  manuscript_original_filename: "group-syntax.pdf",
  rubric_title: "T.I.P. Capstone Format",
  status: "conditionally_ready",
  composite_score: 76,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: "One criterion still needs the instructor's judgment.",
  flag_deduction: 0,
  unresolved_high_flag_count: 1,
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
  results: [],
};

function stubViewer(viewer: ManuscriptViewerOut = VIEWER, report: ReportOut = REPORT) {
  vi.stubGlobal("fetch", stubFetchByPath({
    "/check-runs/5/document": viewer,
    "/check-runs/5/report": report,
    "/check-runs/5/escalated": [],
    "/check-runs/5/flags": [SUMMARY],
    "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
    "/flags/7": FLAG,
  }));
}

describe("SignalDocumentViewerPage", () => {
  it("counts a finding as opened once its evidence is shown here, so the report's review progress moves", async () => {
    window.localStorage.clear();
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?finding=7", path: "/report/:checkRunId/document" });
    await waitFor(() => {
      const stored = Object.keys(window.localStorage).filter((key) => key.startsWith("veridical.flags-viewed.v1."))
        .flatMap((key) => JSON.parse(window.localStorage.getItem(key) ?? "[]") as number[]);
      expect(stored).toContain(7);
    });
  });

  it("keeps the selected record qualification associated outside the bounded reading workspace", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [SUMMARY],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
      "/flags/7": { ...FLAG, llm_mode: "fake" },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?finding=7", path: "/report/:checkRunId/document" });
    await screen.findByText("Test-mode AI responses");
    const frame = screen.getByRole("region", { name: "Manuscript and review" });
    await waitFor(() => expect(frame).toHaveAttribute("aria-describedby"));
    const qualification = document.getElementById(frame.getAttribute("aria-describedby")!)!;
    expect(qualification).toHaveTextContent(/fixtures instead of real AI grading/);
    expect(frame).not.toContainElement(qualification);
    const returnNavigation = screen.getByRole("navigation", { name: "Workspace return" });
    // The way back to the list sits in the bar above the manuscript, outside both panes.
    const bar = qualification.nextElementSibling!;
    expect(bar).toHaveClass("signal-review-bar");
    expect(bar).toContainElement(returnNavigation);
    expect(bar.nextElementSibling).toBe(frame);
    expect(document.querySelectorAll(".signal-flag-mode")).toHaveLength(1);
    expect(document.querySelector(".signal-document-selection-strip")).toBeNull();
  });

  it("keeps an edited pending criterion draft through text, current PDF and selected PDF return", async () => {
    const row: CriterionResultOut = {
      criterion_id: 1, check_result_id: 41, text: "Pending evidence judgment", type: "semantic", kind: "semantic",
      weight: 1, weight_importance: "med", outcome: "escalated", score: null, basis: "llm", anchor: "page 4",
      reasoning: "Recorded uncertainty.", reason: "Needs instructor judgment.",
      evidence: [{ quote: "Recorded paragraph.", anchor: "page 4" }], resolution: null,
    };
    const baseFetch = stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": { ...REPORT, pending_review_count: 1, results: [row] },
      "/check-runs/5/evidence": { snapshot_id: "f".repeat(64), assessment_revision: 0, llm_mode: "real", references: [
        { reference_id: "c".repeat(64), criterion_id: 1, flag_id: null, role: "criterion_evidence", index: 0,
          quote: "Recorded paragraph.", anchor: "page 4", source_scope: "own_manuscript", source_state: "not_checked" } ] },
      [`/check-runs/5/evidence/${"c".repeat(64)}/location`]: { snapshot_id: "f".repeat(64), source_identity: "d".repeat(64), source_format: "pdf",
        placement: { reference_id: "c".repeat(64), state: "page_only", page: 4, paragraph: null, boxes: [], reason: null }, caption: "Page shown without a passage mark." },
      "/check-runs/5/escalated": [{ check_result_id: 41, criterion_id: 1, criterion_text: row.text, assessment_revision: 0,
        weight: 1, agreement: null, votes: [null, null], ai_majority_verdict: null, reason: "Needs instructor judgment.",
        review_reason: "low_confidence", unverified_evidence: null, reasoning: row.reasoning, evidence: row.evidence }],
      "/check-runs/5/flags": [], "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] },
    });
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/page-text")) {
        const page = Number(url.searchParams.get("page"));
        return new Response(JSON.stringify({ check_run_id: 5, manuscript_id: 10, page, page_count: 42,
          state: "available", reason: null, blocks: [{ text: `Reading saved page ${page}.`, is_furniture: false }],
          saved_block_count: 1, omitted_block_count: 0 }));
      }
      return baseFetch(input, init);
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=decision&pane=review&criterion=1", path: "/report/:checkRunId/document" });
    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));
    const reason = await screen.findByRole("textbox", { name: "Comment (optional)" });
    const entered = "  Unsaved instructor judgment\nwith exact whitespace.  ";
    fireEvent.change(reason, { target: { value: entered } });
    const open = screen.getByRole("button", { name: "Show recorded location" });
    await waitFor(() => expect(open).toBeEnabled());
    open.focus(); fireEvent.click(open);
    const manual = screen.getByRole("button", { name: "Continue in full manuscript" }); manual.focus(); fireEvent.click(manual);
    const read = screen.getByRole("button", { name: "Read text" }); read.focus(); fireEvent.click(read);
    await screen.findByText("Reading saved page 4.");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Reading saved page 5.");
    fireEvent.click(screen.getAllByRole("button", { name: "View original PDF p. 5" })[0]);
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-page", "5");
    fireEvent.click(screen.getByRole("button", { name: "Read text" }));
    await screen.findByText("Reading saved page 5.");
    fireEvent.click(screen.getByRole("button", { name: "Return to selected PDF p. 4" }));
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-page", "4");
    const back = screen.getByRole("button", { name: "Return to criterion review" }); back.focus(); fireEvent.click(back);
    expect(screen.getByRole("textbox", { name: "Comment (optional)" })).toHaveValue(entered);
    expect(screen.getByRole("button", { name: "Show recorded location" })).toHaveFocus();
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
  it.each(["", "&finding=7", "&finding=7&anchor=page%209"])("keeps criterion source context neutral with stale selection %s", async stale => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: `/report/5/document?view=decision&pane=review${stale}`, path: "/report/:checkRunId/document" });
    await screen.findByRole("heading", { name: "Readiness" });
    const pane = await screen.findByTestId("pdf-pane");
    expect(pane).toHaveAttribute("data-regions", "0");
    expect(pane).toHaveAttribute("data-selection", "");
    expect(pane).toHaveAttribute("data-page", stale.includes("anchor=") ? "9" : "");
    expect(screen.queryByText("Finding 1 selected")).not.toBeInTheDocument();
  });
  it("keeps criterion return context through PDF text browsing without a judgment write", async () => {
    const row: CriterionResultOut = {
      criterion_id: 1, text: "Recorded source control", type: "structural", kind: "structural",
      weight: 1, weight_importance: "med", outcome: "passed", score: 100,
      basis: null, anchor: "page 4", reasoning: null, reason: null,
      evidence: [{ quote: "Recorded paragraph.", anchor: "page 4" }], resolution: null,
    };
    const fetch = stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": { ...REPORT, results: [row] },
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] },
      "/check-runs/5/document/page-text": { check_run_id: 5, manuscript_id: 10, page: 4, page_count: 42, state: "available", reason: null,
        blocks: [{ text: "Verified page-four reading text.", is_furniture: false }], saved_block_count: 1, omitted_block_count: 0 },
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=decision&step=grades&pane=review", path: "/report/:checkRunId/document" });
    await screen.findByRole("heading", { name: "Criterion records" });
    const details = document.getElementById("signal-criterion-evidence-5-1");
    expect(details).not.toBeNull(); fireEvent.click(details!);
    const open = await screen.findByRole("link", { name: "Open in manuscript: page 4. Criterion 1: Recorded source control" });
    open.focus(); fireEvent.click(open);
    expect(await screen.findByRole("button", { name: "Return to criterion record" })).toBeInTheDocument();
    const read = screen.getByRole("button", { name: "Read text" }); read.focus(); fireEvent.click(read);
    await screen.findByText("Verified page-four reading text.");
    const back = screen.getByRole("button", { name: "Return to criterion record" });
    back.focus(); fireEvent.click(back);
    expect(screen.getByRole("link", { name: "Open in manuscript: page 4. Criterion 1: Recorded source control" })).toHaveFocus();
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
  it.each([undefined, false, true])("shows one record-based support notice and no age/rerun warning when flagged=%s", async (flag) => {
    stubViewer(VIEWER, { ...REPORT, historical_semantic_support_unverified: flag });
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=decision&pane=review", path: "/report/:checkRunId/document" });
    await screen.findByRole("heading", { name: "Readiness" });
    expect(screen.queryAllByText("AI evidence support needs review")).toHaveLength(flag ? 1 : 0);
    expect(screen.queryByText("Historical AI grades need fresh evidence review")).not.toBeInTheDocument();
    expect(screen.queryByText(/suggestions predate criterion-support validation/)).not.toBeInTheDocument();
  });
  it.each(["fake", "real", "unknown"] as const)("BUG-435: decision AI disclosure is scoped to responses for mode %s", async (mode) => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": { ...REPORT, llm_mode: mode },
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 4, matches: [] },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=decision&pane=review", path: "/report/:checkRunId/document" });
    await screen.findByRole("heading", { name: "Readiness" });
    if (mode === "fake") {
      expect(screen.getByText("Test-mode AI responses")).toBeVisible();
      expect(screen.getByText("These fixture responses are not a real manuscript assessment.")).toBeVisible();
      expect(screen.queryByText(/This review was produced with fixture responses/)).not.toBeInTheDocument();
    } else {
      expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
      if (mode === "unknown") expect(screen.getByText("This record does not establish whether responses came from a real external model or simulated fixtures. Check the recorded evidence directly before relying on AI-derived results.")).toBeVisible();
      else expect(screen.queryByText("AI response mode could not be verified")).not.toBeInTheDocument();
    }
  });
  it("shows current historical title guidance before preserved comparison evidence", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": { ...REPORT, historical_citation_title_unverified: true },
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [{ ...SUMMARY, check_kind: "citation_integrity", problem_kind: "possible_metadata_mismatch", citation_metadata_review_warning: "quoted_title_projection_unverified" }],
      "/flags/7": { ...FLAG, check_kind: "citation_integrity", ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: CITATION_COMPARISON, citation_metadata_review_warning: "quoted_title_projection_unverified" },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText("Historical title comparison needs review")).toBeInTheDocument();
    expect(screen.getByText(/original evidence and explanation below are preserved/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Recorded citation metadata comparison" })).toBeInTheDocument();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("V-081: the decision pane counts unassessed criterion records without calling them assessed", async () => {
    const results: CriterionResultOut[] = Array.from({ length: 5 }, (_, index) => ({
      criterion_id: index + 1, text: `Unassessed criterion ${index + 1}`, type: "semantic", kind: "semantic",
      weight: 1, weight_importance: "med", outcome: "quota_exhausted", score: null,
      basis: null, anchor: null, reasoning: null, reason: "Capacity reached", evidence: [], resolution: null,
    }));
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": { ...REPORT, results },
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [],
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=decision&step=grades&pane=review", path: "/report/:checkRunId/document" });
    expect(await screen.findByRole("heading", { name: "Criterion records" })).toBeVisible();
    expect(screen.getByRole("button", { name: /^Step 3: Check recorded grades\. 5 grades · 0 yours/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getAllByText("Not assessed: capacity reached")).toHaveLength(5);
    expect(screen.queryByText("All assessed criteria")).not.toBeInTheDocument();
  });

  it.each([true, false])("V-081: registry evidence is shared with flag detail; missing history is explicit (%s)", async (saved) => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [{ ...SUMMARY, check_kind: "citation_integrity", problem_kind: "possible_metadata_mismatch" }],
      "/flags/7": { ...FLAG, check_kind: "citation_integrity", ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: saved ? CITATION_COMPARISON : null },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText("Registry check result")).toBeInTheDocument();
    expect(screen.getByText(saved ? "Recorded registry metadata comparison" : "Registry comparison without saved provider metadata")).toBeInTheDocument();
    expect(screen.queryByText(/A second passage does not apply/)).not.toBeInTheDocument();
    if (saved) {
      expect(screen.getByText("Cited study title")).toBeInTheDocument();
      expect(screen.getByText("Registered study title")).toBeInTheDocument();
      expect(screen.getByText("abc-saved-policy-hash")).toBeInTheDocument();
    } else expect(screen.getByText("Provider comparison was not saved")).toBeInTheDocument();
  });

  it.each([true, false])("shows saved p-value comparison or recorded fallback without disclosure (fields=%s)", async (hasFields) => {
    const reasoning = "The reported .900 differs from the recalculated .021.";
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [{ ...SUMMARY, check_kind: "statistical_forensics", problem_kind: "p_value_decision_error" }],
      "/flags/7": { ...FLAG, check_kind: "statistical_forensics", ai_verdict_summary: "p_value_decision_error", ai_reasoning: reasoning,
        p_value_comparison: hasFields ? { reported: "= 0.900", recalculated: "0.021" } : null },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByRole("heading", { name: "Recorded p-value comparison" })).toBeVisible();
    if (hasFields) {
      expect(screen.getByText("= 0.900")).toBeVisible();
      expect(screen.getByText("0.021")).toBeVisible();
    } else {
      expect(screen.getByText(reasoning)).toBeVisible();
      expect(screen.queryByText("Recorded reasoning and technical details")).not.toBeInTheDocument();
    }
    expect(screen.getByText(/original analysis output for the test value, degrees of freedom/)).toBeVisible();
    expect(screen.queryByText(/shown sample size and summary statistics/)).not.toBeInTheDocument();
  });

  it("gives missing-reference guidance without inventing a source to open", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [SUMMARY],
      "/flags/7": { ...FLAG, check_kind: "citation_integrity", ai_verdict_summary: "orphan_in_text_citation" },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText(/Check the manuscript's reference list for this author and year/)).toBeVisible();
    expect(screen.queryByText(/Open the cited source and confirm/)).not.toBeInTheDocument();
  });

  it("shows both saved agreement statements with their own source actions, without a false single-location claim", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [], "/check-runs/5/flags": [SUMMARY],
      "/flags/7": { ...FLAG, affects_readiness: false, agreement_pair: { intent_text: "To build a login module.", intent_anchor: "page 1", outcome_text: "The login module was not implemented.", outcome_anchor: "page 2" } },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?finding=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText("Statement B. Selected as a result")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show statement B in manuscript" })).toBeInTheDocument();
    expect(screen.getByText("This review lead does not change readiness.")).toBeInTheDocument();
    expect(screen.queryByText("Single-location check")).not.toBeInTheDocument();
    expect(screen.queryByText(/A second passage does not apply/)).not.toBeInTheDocument();
  });

  it("rejects an invalid route id without issuing a malformed request", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/nope/document", path: "/report/:checkRunId/document" });
    expect(await screen.findByText("This manuscript address is invalid")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("places a recorded criterion page anchor in the source instead of opening an unpositioned document", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?anchor=page%209", path: "/report/:checkRunId/document" });
    const pane = await screen.findByTestId("pdf-pane");
    expect(pane).toHaveAttribute("data-page", "9");
    expect(pane).toHaveAttribute("data-file", expect.stringContaining("/check-runs/5/document/file"));
    expect(pane).toHaveAttribute("data-selection", "");
    expect(pane).toHaveAttribute("data-regions", "0");
    expect(screen.getByText(/not an exact passage highlight/)).toBeInTheDocument();
  });

  it.each(["page 999", "page 0", "page 9 and page 12", "appendix page 9", "page 9007199254740992"])("does not pretend to place unsupported source anchor %s", async (anchor) => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: `/report/5/document?anchor=${encodeURIComponent(anchor)}`, path: "/report/:checkRunId/document" });
    expect(await screen.findByText("The recorded criterion anchor could not be placed")).toBeInTheDocument();
    expect(await screen.findByTestId("pdf-pane")).toHaveAttribute("data-page", "");
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-regions", "0");
  });

  it("keeps a finding selected while opening a different recorded statement page without its original highlight", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?finding=7&anchor=page%209", path: "/report/:checkRunId/document" });
    expect(await screen.findByTestId("pdf-pane")).toHaveAttribute("data-page", "9");
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "");
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-regions", "0");
    expect(await screen.findByText("Finding 1 of 1")).toBeInTheDocument();
    expect(document.querySelector(".signal-document-selection-strip")).toBeNull();
  });

  it("keeps unsupported recorded anchor recovery visible without inventing a page", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?anchor=appendix%20%26%20%3Cfigure%3E", path: "/report/:checkRunId/document" });
    expect(await screen.findByText("The recorded criterion anchor could not be placed")).toBeInTheDocument();
    expect(screen.getByText(/inspect the manuscript manually/i)).toBeInTheDocument();
    expect(await screen.findByTestId("pdf-pane")).toHaveAttribute("data-page", "");
  });

  it("shows selected bounded evidence and instructor actions beside its source page", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText(/abstract and findings list different participant totals/i)).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-page", "12");
    expect(screen.getByRole("button", { name: "Dismiss finding" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Review instructor actions for this location" })).not.toBeInTheDocument();
    expect(screen.queryByText(/% textual similarity/)).not.toBeInTheDocument();
  });

  it("BUG-170: never renders a whole-document reuse flag's system-authored summary sentence as a manuscript quote", async () => {
    // UX review finding: this panel used to render evidence_excerpt inside
    // a <blockquote> unconditionally -- for a whole-document reuse flag
    // that's a system-authored template sentence, never a quote, the
    // exact defect this ticket fixed on FlagDetail.tsx but had left live
    // here, one click away. A real passage_pair is attached (BUG-153), so
    // real evidence still exists just below the (now-honest) reasoning.
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER,
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [SUMMARY],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
      "/flags/7": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_high_similarity",
        is_passage_level: false,
        evidence_excerpt: "This manuscript shows high textual similarity to archived manuscript #3 in VERIDICAL's shared originality library: possible shared content or reuse. Please verify manually.",
        ai_reasoning: null,
        page_anchor: "whole document",
        passage_pair: {
          own_excerpt: "Chapter 3: Methodology text.",
          own_context_before: null,
          own_context_after: null,
          matched_ref: 3,
          matched_excerpt: "Chapter 3: Research Methodology text.",
          matched_context_before: null,
          matched_context_after: null,
          context_words_each_side: 60,
          similarity: 0.91,
          level: "high_similarity",
        },
      },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });

    expect((await screen.findAllByText(/possible shared content or reuse/)).length).toBeGreaterThan(0);
    expect(document.querySelector("blockquote")).not.toBeInTheDocument();
    // The system's explanation is shown under its own heading, not hidden in a disclosure.
    expect(screen.getByRole("heading", { name: "What the check found" })).toBeInTheDocument();
  });

  it("BUG-170: shows an honest explanation, not a mislabeled quote, for a resubmission flag with no passage to compare", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER,
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [SUMMARY],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
      "/flags/7": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_same_instructor_resubmission",
        is_passage_level: false,
        severity: "low",
        evidence_excerpt: "This manuscript appears to be the same document as your own earlier upload, archived manuscript #4.",
        ai_reasoning: null,
        page_anchor: "whole document",
        passage_pair: null,
      },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });

    // Without the fix, this screen showed the mislabeled quote and
    // NOTHING else (no ai_reasoning, no fallback) -- the resubmission
    // sentence must still reach the instructor, honestly labeled. Both
    // this sentence AND the Alert's own copy mention "your own earlier
    // upload," so this is queried by its distinct "archived manuscript
    // #4" tail rather than the shared phrase.
    expect((await screen.findAllByText(/archived manuscript #4/)).length).toBeGreaterThan(0);
    expect(document.querySelector("blockquote")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What the check found" })).toBeInTheDocument();
    expect(screen.getByText("No passage pair is available")).toBeInTheDocument();
    expect(screen.getByText(/then decide whether the possible overlap has a legitimate source/)).toBeInTheDocument();
    expect(screen.queryByText(/Compare the passages and decide/)).not.toBeInTheDocument();
  });

  it("keeps finding navigation within the selected module and switches categories beside the manuscript", async () => {
    const secondSummary: FlagSummaryOut = {
      ...SUMMARY,
      id: 8,
      check_kind: "citation_integrity",
      severity: "med",
      evidence_excerpt: "A cited source needs instructor verification.",
      page_anchor: "page 18",
      problem_kind: "citation_unverified",
    };
    const secondFlag: FlagOut = {
      ...FLAG,
      id: 8,
      check_kind: "citation_integrity",
      severity: "med",
      evidence_excerpt: secondSummary.evidence_excerpt,
      page_anchor: secondSummary.page_anchor,
      ai_verdict_summary: "citation_unverified",
    };
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": {
        ...VIEWER,
        regions: [
          ...VIEWER.regions,
          { flag_id: 8, kind: "page_only", page: 18, end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null },
        ],
      },
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [SUMMARY, secondSummary],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
      "/flags/7": FLAG,
      "/flags/8": secondFlag,
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=finding&finding=7&location=7", path: "/report/:checkRunId/document" });

    expect(await screen.findByRole("heading", { name: "Possible internal contradiction" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next finding" })).not.toBeInTheDocument();
    // Inside one finding the check tiles give way to the evidence; the list holds them.
    fireEvent.click(screen.getByRole("button", { name: "Back to findings list" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Citation integrity/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Finding 2.*Possible citation issue/ }));
    expect(await screen.findByRole("heading", { name: "Possible citation issue" })).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "8");
    expect(screen.queryByRole("button", { name: "Previous finding" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to findings list" }));
    fireEvent.click(await screen.findByRole("button", { name: /^Internal agreement/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Finding 1.*Possible internal contradiction/ }));
    expect(await screen.findByRole("heading", { name: "Possible internal contradiction" })).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "7");
  });

  it("keeps unavailable source content honest while preserving a return to the report", async () => {
    stubViewer({ ...VIEWER, available: false, unavailable_reason: "Stored content was removed by the instructor.", purged_at: null });
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });
    expect(await screen.findByRole("heading", { name: "Source manuscript unavailable" })).toBeInTheDocument();
    expect(screen.getByText("Stored content was removed by the instructor.")).toBeInTheDocument();
    expect(screen.getByText(/VERIDICAL cannot show the full manuscript/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Integrity findings" })).toBeInTheDocument();
  });

  it("retains the recorded location and citation comparison after source deletion", async () => {
    const deletedAt = "2026-10-21T17:00:00Z";
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": { ...VIEWER, available: false, regions: [], purged_at: deletedAt, unavailable_reason: "Old UTC-date server wording must not be displayed." },
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [{ ...SUMMARY, page_anchor: "paragraph 5", check_kind: "citation_integrity", problem_kind: "possible_metadata_mismatch" }],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] },
      "/flags/7": { ...FLAG, page_anchor: "paragraph 5", check_kind: "citation_integrity", ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: CITATION_COMPARISON },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=finding&finding=7&location=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText(`The stored source file was deleted on ${localDate(deletedAt)} and can no longer be opened.`)).toBeInTheDocument();
    expect((await screen.findAllByText("Original source deleted")).length).toBeGreaterThan(0);
    expect(screen.getByText(/cannot display or highlight this location/)).toBeInTheDocument();
    expect(screen.getAllByText("Extracted paragraph 6").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Recorded citation metadata comparison" })).toBeInTheDocument();
    expect(screen.queryByText(/could not place it on a page or paragraph/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Old UTC-date server wording/)).not.toBeInTheDocument();
  });

  it("does not claim the other pane is available when both source and review requests fail", async () => {
    const failure = () => new Response(JSON.stringify({ error: { code: "internal", message: "failed" } }), { status: 500 });
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": failure(),
      "/check-runs/5/report": failure(),
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": failure(),
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });

    expect(await screen.findByText("The manuscript could not be loaded")).toBeInTheDocument();
    expect(await screen.findByText("Could not load review details")).toBeInTheDocument();
    expect(screen.getByText(/source pane loads separately and may also need to be retried/i)).toBeInTheDocument();
    expect(screen.getByText(/review pane loads separately and may also need to be retried/i)).toBeInTheDocument();
    expect(screen.queryByText("The manuscript is still available.")).not.toBeInTheDocument();
    expect(screen.queryByText("The recorded review is still available.")).not.toBeInTheDocument();
  });

  it("replaces an internal DOCX paragraph ordinal with instructor-facing location copy", async () => {
    const paragraphSummary = { ...SUMMARY, page_anchor: "\u00b60", severity: "med" as const };
    const paragraphFlag = { ...FLAG, page_anchor: "\u00b60", severity: "med" as const };
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": {
        ...VIEWER,
        original_filename: "group-syntax.docx",
        source_format: "docx",
        page_count: null,
        regions: [{ flag_id: 7, kind: "paragraph_only", page: null, end_page: null, bbox: null, all_bboxes: [], paragraph: 0, index: null }],
      },
      "/check-runs/5/document/paragraphs": { paragraphs: [{ paragraph: 0, text: "Methodology", heading_level: 1 }] },
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [paragraphSummary],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] },
      "/flags/7": paragraphFlag,
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=finding&finding=7&location=7&pane=review", path: "/report/:checkRunId/document" });

    expect(await screen.findByRole("heading", { name: "Possible internal contradiction" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous finding" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next finding" })).not.toBeInTheDocument();
    expect(screen.queryByText("\u00b60")).not.toBeInTheDocument();
    expect((await screen.findAllByText("Extracted paragraph 1")).length).toBeGreaterThan(0);
  });

  it("keeps readiness, criteria, signals, and decision work beside the manuscript", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": VIEWER,
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": [],
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?pane=analysis", path: "/report/:checkRunId/document" });

    // Nothing is waiting, so the workspace opens on the decision step.
    expect(await screen.findByText(REPORT.reason as string)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Record the decision on the readiness report" })).toHaveAttribute("href", "/report/5#final-decision");
    expect(screen.getByRole("button", { name: /^Step 4: Decision summary/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("tab", { name: /^Review/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: /^Continue:/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back: Check recorded grades" }));
    expect(await screen.findByRole("heading", { name: "Criterion records" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Record the decision on the readiness report" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Step 2: Inspect findings/ }));
    expect(await screen.findByRole("heading", { name: "No saved check status" })).toBeInTheDocument();
    expect(screen.getByText(/An empty list does not mean the manuscript passed/)).toBeVisible();
    expect(screen.queryByText(REPORT.reason as string)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue: Check recorded grades" }));
    expect(await screen.findByRole("heading", { name: "Criterion records" })).toBeInTheDocument();
    // One way to the report from this workspace, not one per panel.
    expect(screen.getAllByRole("link", { name: /readiness report/ })).toHaveLength(1);
  });

  it("stays on the findings step when another check is chosen, even with criteria waiting", async () => {
    stubViewer(VIEWER, { ...REPORT, pending_review_count: 2 });
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=queue&pane=review", path: "/report/:checkRunId/document" });
    const modules = await screen.findByRole("navigation", { name: "Manuscript check modules" });
    const other = within(modules).getAllByRole("button").find(button => button.getAttribute("aria-pressed") !== "true")!;
    fireEvent.click(other);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Step 2: Inspect findings/ })).toHaveAttribute("aria-current", "step"));
    expect(screen.queryByRole("heading", { name: "Criteria needing your judgment" })).not.toBeInTheDocument();
  });

  it("opens the manuscript first and preserves manual analysis tabs on narrow layouts", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });
    const manuscriptTab = await screen.findByRole("tab", { name: "Manuscript" });
    const analysisTab = screen.getByRole("tab", { name: /^Review/ });
    expect(manuscriptTab).toHaveAttribute("aria-selected", "true");
    expect(analysisTab).toHaveAttribute("aria-selected", "false");
    fireEvent.click(analysisTab);
    expect(analysisTab).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(analysisTab, { key: "Home" });
    expect(manuscriptTab).toHaveAttribute("aria-selected", "false");
    expect(analysisTab).toHaveAttribute("aria-selected", "true");
    expect(manuscriptTab).toHaveFocus();
    fireEvent.keyDown(manuscriptTab, { key: "ArrowRight" });
    expect(analysisTab).toHaveFocus();
    fireEvent.keyDown(analysisTab, { key: "ArrowRight" });
    expect(manuscriptTab).toHaveFocus();
    fireEvent.click(manuscriptTab);
    expect(manuscriptTab).toHaveAttribute("aria-selected", "true");
    expect(manuscriptTab).toHaveFocus();
  });

  it("presents one stable numbered finding for stored whole, section, and passage reuse locations", async () => {
    const reuseSummaries: FlagSummaryOut[] = [
      { ...SUMMARY, id: 109, check_kind: "originality_reuse", problem_kind: "reuse_high_similarity", matched_ref: 3, evidence_excerpt: "Whole-manuscript comparison.", page_anchor: "whole document" },
      { ...SUMMARY, id: 110, check_kind: "originality_reuse", problem_kind: "reuse_high_similarity_chapter", matched_ref: 3, evidence_excerpt: "Section comparison.", page_anchor: "Chapter 3" },
      { ...SUMMARY, id: 111, check_kind: "originality_reuse", problem_kind: "reuse_high_similarity_passage", matched_ref: 3, evidence_excerpt: "The system uses a hybrid rule-based and AI approach.", page_anchor: "page 12", is_passage_level: true },
    ];
    const passageFlag: FlagOut = {
      ...FLAG,
      id: 111,
      check_kind: "originality_reuse",
      ai_verdict_summary: "reuse_high_similarity_passage",
      evidence_excerpt: reuseSummaries[2].evidence_excerpt,
      page_anchor: "page 12",
      is_passage_level: true,
      passage_pair: {
        own_excerpt: "The system uses a hybrid rule-based and AI approach for review.",
        own_context_before: null,
        own_context_after: null,
        matched_ref: 3,
        matched_excerpt: "A prior project says the system uses a hybrid rule-based and AI approach during review.",
        matched_context_before: null,
        matched_context_after: null,
        context_words_each_side: 60,
        similarity: 0.91,
        level: "high_similarity",
      },
    };
    vi.stubGlobal("fetch", stubFetchByPath({
      "/check-runs/5/document": {
        ...VIEWER,
        regions: [
          { flag_id: 109, kind: "whole_document", page: null, end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null },
          { flag_id: 110, kind: "section", page: 7, end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null },
          { flag_id: 111, kind: "page_bbox", page: 12, end_page: null, bbox: [10, 10, 80, 30], all_bboxes: [[10, 10, 80, 30]], paragraph: null, index: null },
        ],
      },
      "/check-runs/5/report": REPORT,
      "/check-runs/5/escalated": [],
      "/check-runs/5/flags": reuseSummaries,
      "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
      "/flags/111": passageFlag,
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?pane=review", path: "/report/:checkRunId/document" });

    fireEvent.click(await screen.findByRole("button", { name: /^Originality and reuse/ }));
    expect(await screen.findByRole("heading", { name: "Integrity findings" })).toBeInTheDocument();
    expect(screen.getByText("3 recorded locations or scopes")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /Finding 1.*High passage similarity/s });
    fireEvent.click(row);

    expect(document.querySelector(".signal-document-selection-strip")).toBeNull();
    expect(await screen.findByRole("heading", { name: "High passage similarity" })).toBeInTheDocument();
    expect(await screen.findByText("Location 3 of 3. These records are presented as one finding; none of the stored data was removed.")).toBeInTheDocument();
    expect(screen.getByText(/Shaded wording appears in both stored passages/)).toBeInTheDocument();
    const comparison = screen.getByText("Passage comparison", { selector: "h3" });
    const reasoning = screen.getByRole("heading", { name: "What the check found" });
    expect(comparison.compareDocumentPosition(reasoning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelectorAll("mark.reuse-shared-wording").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "111");

    fireEvent.click(screen.getByRole("button", { name: "Back to findings list" }));
    expect(await screen.findByRole("heading", { name: "Integrity findings" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Finding 1.*High passage similarity/s })).toHaveFocus());
  });
});

it("retains the workspace record and focus with qualified readiness across a transient report read failure", async () => {
  let client: QueryClient; let failed = false;
  function QueryAccess() { client = useQueryClient(); return null; }
  const base = stubFetchByPath({ "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
    "/check-runs/5/escalated": [], "/check-runs/5/flags": [],
    "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] } });
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (failed && String(input).endsWith("/report")) return new Response(JSON.stringify({ error: { code: "unavailable", message: "Controlled workspace read failure" } }), { status: 503 });
    return base(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  renderWithProviders(<><QueryAccess /><SignalDocumentViewerPage /></>, { route: "/report/5/document?view=decision&step=decision&pane=review", path: "/report/:checkRunId/document" });
  const heading = await screen.findByRole("heading", { name: "Readiness" }); heading.focus();
  failed = true; await act(async () => { await client.invalidateQueries({ queryKey: ["report", 5], exact: true }); });
  await screen.findByText("Could not update this report");
  expect(heading.isConnected).toBe(true); expect(heading).toHaveFocus();
  expect(screen.getByText("Last loaded system readiness")).toBeVisible();
  expect(screen.getByRole("link", { name: "Record the decision on the readiness report" })).toBeInTheDocument();
  failed = false; fireEvent.click(screen.getAllByRole("button", { name: "Retry report" })[0]);
  await waitFor(() => expect(screen.queryByText("Could not update this report")).not.toBeInTheDocument());
  expect(heading).toHaveFocus();
  vi.unstubAllGlobals();
});

it.each([401, 403, 404, 503])("keeps the evidence queue only for a recoverable report read (%s)", async (status) => {
  let client!: QueryClient; let failed = false;
  function QueryAccess() { client = useQueryClient(); return null; }
  const base = stubFetchByPath({ "/check-runs/5/document": VIEWER, "/check-runs/5/report": REPORT,
    "/check-runs/5/escalated": [], "/check-runs/5/flags": [SUMMARY],
    "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 0, matches: [] } });
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (failed && String(input).endsWith("/report")) return new Response(JSON.stringify({ error: { code: status === 503 ? "unavailable" : "not_available", message: "Controlled report read failure" } }), { status });
    return base(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  renderWithProviders(<><QueryAccess /><SignalDocumentViewerPage /></>, { route: "/report/5/document?pane=review", path: "/report/:checkRunId/document" });
  const heading = await screen.findByRole("heading", { name: "Integrity findings" });
  const row = screen.getByRole("button", { name: /Finding 1.*Possible internal contradiction/s }); row.focus();
  failed = true; await act(async () => { await client.invalidateQueries({ queryKey: ["report", 5], exact: true }); });
  if (status === 503) {
    expect(await screen.findByText("Could not update this report")).toBeVisible();
    expect(heading.isConnected).toBe(true); expect(row).toHaveFocus();
    expect(screen.getByText(/Showing the last loaded report/)).toBeVisible();
    failed = false; fireEvent.click(screen.getByRole("button", { name: "Retry report" }));
    await waitFor(() => expect(screen.queryByText("Could not update this report")).not.toBeInTheDocument());
    expect(row).toHaveFocus();
  } else {
    await waitFor(() => expect(heading.isConnected).toBe(false));
    expect(row.isConnected).toBe(false);
    expect(screen.queryByRole("heading", { name: "Comparison leads" })).not.toBeInTheDocument();
    expect(screen.queryByText("Readiness and decision")).not.toBeInTheDocument();
  }
  expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  vi.unstubAllGlobals();
});
