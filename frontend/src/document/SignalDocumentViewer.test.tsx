import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut, FlagSummaryOut, ManuscriptViewerOut } from "../api/types";
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
  passage_pair: null,
  first_upload_context: false,
  evidence_unavailable: false,
  citation_source_key: null,
  confirmed_citation_source: false,
};

function stubViewer(viewer: ManuscriptViewerOut = VIEWER) {
  vi.stubGlobal("fetch", stubFetchByPath({
    "/check-runs/5/document": viewer,
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
    expect(screen.getByRole("link", { name: "Review full signal and instructor actions" })).toHaveAttribute("href", "/flags/7");
    expect(screen.queryByText(/% textual similarity/)).not.toBeInTheDocument();
  });

  it("keeps unavailable source content honest while preserving a return to the report", async () => {
    stubViewer({ ...VIEWER, available: false, unavailable_reason: "Stored content was removed by the instructor.", purged_at: "2026-08-20T00:00:00Z" });
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });
    expect(await screen.findByRole("heading", { name: "The stored manuscript is unavailable" })).toBeInTheDocument();
    expect(screen.getByText("Stored content was removed by the instructor.")).toBeInTheDocument();
    expect(screen.getByText(/Missing source content is not treated as verified/)).toBeInTheDocument();
  });

  it("uses one manual tab set on narrow layouts instead of duplicating both long panes", async () => {
    stubViewer();
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document", path: "/report/:checkRunId/document" });
    const documentTab = await screen.findByRole("tab", { name: "Document" });
    const evidenceTab = screen.getByRole("tab", { name: "Evidence (1)" });
    expect(evidenceTab).toHaveAttribute("aria-selected", "true");
    fireEvent.click(documentTab);
    expect(documentTab).toHaveAttribute("aria-selected", "true");
  });
});
