import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut, ManuscriptViewerOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { OwnedManuscriptSourcePane } from "./ManuscriptSourcePane";

vi.mock("./PdfSourceReader", () => ({ PdfSourceReader: ({ requestedPage, regions, selectedFlagId }: { requestedPage: number | null; regions: unknown[]; selectedFlagId: number | null }) => <div data-testid="source-pdf" data-page={requestedPage ?? ""} data-regions={regions.length} data-selection={selectedFlagId ?? ""}>Original PDF</div> }));
vi.mock("./DocxPane", () => ({ DocxPane: ({ requestedParagraph, regions }: { requestedParagraph: number | null; regions: unknown[] }) => <div data-testid="source-docx" data-paragraph={requestedParagraph ?? ""} data-regions={regions.length}>Original extracted DOCX</div> }));

const FLAG: FlagOut = {
  assessment_revision: 0,
  id: 7, check_result_id: 9, check_run_id: 5, manuscript_group_label: "Source control", check_kind: "internal_agreement",
  criterion_text: null, severity: "med", confidence: null, evidence_excerpt: "Saved objective.", page_anchor: "page 1",
  annotation: null, overridden: false, override_reason: null, ai_verdict_summary: "possible_mismatch", ai_reasoning: null,
  llm_mode: "fake", is_passage_level: false, passage_pair: null, first_upload_context: false, evidence_unavailable: false,
  citation_source_key: null, confirmed_citation_source: false,
};
const VIEWER: ManuscriptViewerOut = {
  manuscript_id: 10, original_filename: "source.pdf", source_format: "pdf", available: true, unavailable_reason: null, purged_at: null, page_count: 4,
  regions: [{ flag_id: 7, kind: "page_bbox", page: 1, end_page: null, bbox: [1, 2, 3, 4], all_bboxes: [[1, 2, 3, 4]], paragraph: null, index: null }],
};
function setup(anchor?: string | null, viewer = VIEWER, overrides: Record<string, unknown> = {}) {
  const fetch = stubFetchByPath({ "/auth/me": { id: 1 }, "/check-runs/5/document": viewer,
    "/check-runs/5/flags": [{ ...FLAG, problem_kind: "possible_mismatch" }],
    "/check-runs/5/document/paragraphs": { paragraphs: [{ paragraph: 0, text: "First saved body item.", heading_level: null }] }, ...overrides });
  vi.stubGlobal("fetch", fetch);
  const render = renderWithProviders(<OwnedManuscriptSourcePane flag={FLAG} anchor={anchor} onSelectFlag={() => undefined} />, { route: "/flags/7", path: "/flags/:flagId", authInstructorId: 1 });
  return { ...render, fetch };
}
afterEach(() => vi.unstubAllGlobals());

describe("owned manuscript source in finding review", () => {
  it("shows the owned source with ordinary saved regions after navigation identity loads", async () => {
    const { fetch } = setup();
    await waitFor(() => expect(screen.getByTestId("source-pdf")).toHaveAttribute("data-regions", "1"));
    expect(screen.getByTestId("source-pdf")).toHaveAttribute("data-page", "1");
    expect(fetch.mock.calls.every(([input]) => !String(input).includes("document/paragraphs"))).toBe(true);
  });
  it("opens a statement's own page without borrowing the finding's exact highlight", async () => {
    setup("page 3");
    const pdf = await screen.findByTestId("source-pdf");
    expect(pdf).toHaveAttribute("data-page", "3");
    expect(pdf).toHaveAttribute("data-regions", "0");
    expect(screen.getByText(/stored this page, not an exact passage/)).toBeInTheDocument();
  });
  it.each(["page 0", "page 5", "page 9007199254740992", "unplaced section"])("does not substitute another statement location for %s", async anchor => {
    setup(anchor);
    expect(await screen.findByTestId("source-pdf")).toHaveAttribute("data-page", "");
    expect(screen.getByTestId("source-pdf")).toHaveAttribute("data-regions", "0");
    expect(screen.getByText(/selected statement.*could not be placed/i)).toBeInTheDocument();
  });
  it("accepts paragraph zero only when it is a real saved DOCX body item", async () => {
    setup("paragraph 0", { ...VIEWER, source_format: "docx", page_count: null, regions: [] });
    await waitFor(() => expect(screen.getByTestId("source-docx")).toHaveAttribute("data-paragraph", "0"));
    expect(screen.getByTestId("source-docx")).toHaveAttribute("data-regions", "0");
  });
  it("refuses a missing DOCX body item instead of moving to paragraph zero", async () => {
    setup("paragraph 1", { ...VIEWER, source_format: "docx", page_count: null, regions: [] });
    expect(await screen.findByTestId("source-docx")).toHaveAttribute("data-paragraph", "");
    await screen.findByText(/selected statement.*could not be placed/i);
  });
  it("keeps the source readable after a failed collection read without inventing Finding 1", async () => {
    setup(undefined, VIEWER, { "/check-runs/5/flags": new Response(JSON.stringify({ error: { code: "unavailable", message: "Controlled list failure" } }), { status: 503 }) });
    expect(await screen.findByTestId("source-pdf")).toHaveAttribute("data-regions", "0");
    await screen.findByText(/saved location for Record #7/);
    expect(screen.queryByText(/Finding 1/)).not.toBeInTheDocument();
  });
  it("distinguishes retained evidence from a deleted original source", async () => {
    setup(undefined, { ...VIEWER, available: false, purged_at: "2026-10-01T00:00:00Z", regions: [] });
    await screen.findByRole("heading", { name: "Source manuscript unavailable" });
    expect(screen.getByText(/stored source file was deleted/)).toBeInTheDocument();
    expect(screen.queryByTestId("source-pdf")).not.toBeInTheDocument();
  });
});
