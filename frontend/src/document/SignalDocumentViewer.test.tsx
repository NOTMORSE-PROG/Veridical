import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut, FlagSummaryOut, ManuscriptViewerOut, ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";

vi.mock("./PdfPane", () => ({
  PdfPane: ({ requestedPage, selectedFlagId, fileUrl }: { requestedPage: number | null; selectedFlagId: number | null; fileUrl: string }) => (
    <div data-testid="pdf-pane" data-page={requestedPage ?? ""} data-selection={selectedFlagId ?? ""} data-file={fileUrl}>PDF source</div>
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

function stubViewer(viewer: ManuscriptViewerOut = VIEWER) {
  vi.stubGlobal("fetch", stubFetchByPath({
    "/check-runs/5/document": viewer,
    "/check-runs/5/report": REPORT,
    "/check-runs/5/escalated": [],
    "/check-runs/5/flags": [SUMMARY],
    "/check-runs/5/document/reuse-matches": { passage_archive_size_n: 8, matches: [] },
    "/flags/7": FLAG,
  }));
}

describe("SignalDocumentViewerPage", () => {
  afterEach(() => vi.unstubAllGlobals());

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
  });

  it("shows selected bounded evidence, places its page, and links to the full instructor action record", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?flag=7", path: "/report/:checkRunId/document" });
    expect(await screen.findByText(/abstract and findings list different participant totals/i)).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-page", "12");
    expect(screen.getByRole("link", { name: "Review instructor actions for this location" })).toHaveAttribute("href", "/flags/7");
    expect(screen.queryByText(/% textual similarity/)).not.toBeInTheDocument();
  });

  it("BUG-170: never renders a whole-document reuse flag's system-authored summary sentence as a manuscript quote", async () => {
    // ux-critic finding: this panel used to render evidence_excerpt inside
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
    expect(screen.getByText("Recorded reasoning and technical details")).toBeInTheDocument();
    expect(document.querySelector("details.signal-document-recorded-reasoning")).not.toHaveAttribute("open");
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
    expect(screen.getByText("Recorded reasoning and technical details")).toBeInTheDocument();
    expect(screen.getByText("No passage pair is available")).toBeInTheDocument();
    expect(screen.getByText(/then decide whether the possible overlap has a legitimate source/)).toBeInTheDocument();
    expect(screen.queryByText(/Compare the passages and decide/)).not.toBeInTheDocument();
  });

  it("moves to the previous and next stable finding without returning to the report", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Next finding" }));
    expect(await screen.findByRole("heading", { name: "Possible citation issue" })).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "8");
    fireEvent.click(screen.getByRole("button", { name: "Previous finding" }));
    expect(await screen.findByRole("heading", { name: "Possible internal contradiction" })).toBeInTheDocument();
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "7");
  });

  it("keeps unavailable source content honest while preserving a return to the report", async () => {
    stubViewer({ ...VIEWER, available: false, unavailable_reason: "Stored content was removed by the instructor.", purged_at: "2026-08-20T00:00:00Z" });
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });
    expect(await screen.findByRole("heading", { name: "Source manuscript unavailable" })).toBeInTheDocument();
    expect(screen.getByText("Stored content was removed by the instructor.")).toBeInTheDocument();
    expect(screen.getByText(/VERIDICAL cannot show the full manuscript/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Review queue" })).toBeInTheDocument();
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
    expect(screen.getAllByText("Reconstructed text").length).toBeGreaterThan(0);
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

    expect(await screen.findByRole("heading", { name: "Review queue" })).toBeInTheDocument();
    expect(screen.getByText("No integrity findings were recorded")).toBeInTheDocument();
    expect(screen.queryByText(REPORT.reason as string)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Final defense-readiness decision" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Review" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: "Review readiness and decision" }));
    expect(await screen.findByText(REPORT.reason as string)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Final defense-readiness decision" })).toBeInTheDocument();
    for (const link of screen.getAllByRole("link", { name: "Open full readiness report" })) {
      expect(link).toHaveAttribute("href", "/report/5");
    }
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

    expect(await screen.findByText("1 finding. 0 resolved.")).toBeInTheDocument();
    expect(screen.getByText("3 recorded locations or scopes")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /Finding 1.*High passage similarity/s });
    fireEvent.click(row);

    expect(await screen.findByText("Finding 1 selected")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "High passage similarity" })).toBeInTheDocument();
    expect(screen.getByText("Location 3 of 3. These records are presented as one finding; none of the stored data was removed.")).toBeInTheDocument();
    expect(screen.getByText(/Yellow marks the selected manuscript location/)).toBeInTheDocument();
    const comparison = screen.getByText("Passage comparison", { selector: "h3" });
    const reasoning = screen.getByText("Recorded reasoning and technical details");
    expect(comparison.compareDocumentPosition(reasoning) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(document.querySelectorAll("mark.reuse-shared-wording").length).toBeGreaterThanOrEqual(2);
    expect(document.querySelector("details.signal-document-recorded-reasoning")).not.toHaveAttribute("open");
    expect(screen.getByTestId("pdf-pane")).toHaveAttribute("data-selection", "111");

    fireEvent.click(screen.getByRole("button", { name: "Back to review queue" }));
    expect(await screen.findByRole("heading", { name: "Review queue" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Finding 1.*High passage similarity/s })).toHaveFocus());
  });
});
