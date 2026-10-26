import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EvidenceItem, ManuscriptViewerOut, ReportOut } from "../api/types";
import { sampleReport, WORKSPACE_SAMPLE } from "../demo/workspaceFixture";
import { evidenceSourceHash } from "../demo/sourceEvidence";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { CriterionEvidenceDialog } from "./CriterionEvidenceDialog";

vi.mock("../document/PdfPane", () => ({ PdfPane: ({ requestedPage, regions }: { requestedPage: number; regions: { kind: string }[] }) => <div data-testid="original-pdf" data-page={requestedPage} data-precision={regions[0].kind} /> }));
vi.mock("../document/DocxPane", () => ({ DocxPane: ({ requestedParagraph }: { requestedParagraph: number }) => <div data-testid="original-docx" data-paragraph={requestedParagraph} /> }));
afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });
const viewer: ManuscriptViewerOut = { manuscript_id: 1, original_filename: WORKSPACE_SAMPLE.filename, available: true, source_format: "pdf", page_count: 109, source_sha256: evidenceSourceHash, unavailable_reason: null, purged_at: null, regions: [] };
function show({ report = sampleReport, metadata = viewer, evidence = report.results[0].evidence[1], index = 1, sample = false }: { report?: ReportOut; metadata?: ManuscriptViewerOut; evidence?: EvidenceItem; index?: number; sample?: boolean } = {}) {
  if (sample) window.history.replaceState({}, "", "/demo/report/1");
  vi.stubGlobal("fetch", stubFetchByPath({ [`/check-runs/${report.check_run_id}/document`]: metadata, [`/check-runs/${report.check_run_id}/document/paragraphs`]: { paragraphs: [{ paragraph: 0, text: "Source text", heading_level: null }] } }));
  return renderWithProviders(<CriterionEvidenceDialog report={report} selection={{ criterionId: report.results[0].criterion_id, evidence, index }} onClose={vi.fn()} />);
}

it("uses a page-only location for a normal saved anchor without inventing a box", async () => {
  show();
  expect(await screen.findByTestId("original-pdf")).toHaveAttribute("data-page", "5");
  expect(screen.getByTestId("original-pdf")).toHaveAttribute("data-precision", "page_only");
});
it("uses the complete source-bound sample passage", async () => {
  show({ sample: true });
  expect(await screen.findByTestId("original-pdf")).toHaveAttribute("data-page", "5");
  expect(screen.getByTestId("original-pdf")).toHaveAttribute("data-precision", "page_bbox");
});
it.each(["hash", "manuscript", "run", "index", "quote"])("refuses a mismatching saved sample %s without opening page1", async mismatch => {
  const report = structuredClone(sampleReport);
  const metadata = { ...viewer };
  let index = 1;
  const evidence = { ...report.results[0].evidence[1] };
  if (mismatch === "hash") metadata.source_sha256 = "wrong-source";
  if (mismatch === "manuscript") metadata.manuscript_id = 2;
  if (mismatch === "run") report.check_run_id = 2;
  if (mismatch === "index") index = 0;
  if (mismatch === "quote") evidence.quote += " Not in source.";
  show({ sample: true, report, metadata, evidence, index });
  await screen.findByText("This passage could not be placed");
  expect(screen.queryByTestId("original-pdf")).not.toBeInTheDocument();
});
it("retains a quotation with an unplaceable anchor instead of choosing an unrelated page", async () => {
  const report = structuredClone(sampleReport);
  report.results[0].evidence[1].anchor = "pages 37-50";
  show({ report });
  await screen.findByText("This passage could not be placed");
  expect(screen.queryByTestId("original-pdf")).not.toBeInTheDocument();
  expect(screen.getByText("pages 37-50")).toBeInTheDocument();
});
it("opens DOCX paragraph zero as a paragraph without invented printed pages", async () => {
  const report = structuredClone(sampleReport);
  report.results[0].evidence[1].anchor = "paragraph 0";
  show({ report, metadata: { ...viewer, source_format: "docx", page_count: null } });
  expect(await screen.findByTestId("original-docx")).toHaveAttribute("data-paragraph", "0");
});
