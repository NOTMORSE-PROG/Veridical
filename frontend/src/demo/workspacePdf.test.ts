import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { buildWorkspacePdf, pdfFindingState, pdfGrade } from "./workspacePdf";
import { sampleFlags, sampleReport } from "./workspaceFixture";

const inputs = () => ({ report: structuredClone(sampleReport), flags: structuredClone(sampleFlags), fileAvailable: false, fetchAsset: vi.fn<typeof fetch>() });
afterEach(() => vi.unstubAllGlobals());

describe("instructor PDF record and source boundaries", () => {
  it.each(["escalated", "unverifiable", "quota_exhausted", "api_down"] as const)("never presents a retained level as a grade for %s", outcome => {
    expect(pdfGrade({ ...sampleReport.results[0], outcome, level: { name: "Above Average", ordinal: 4, points: 2, max_points: 2 } })).toBe("Not graded");
  });
  it("keeps saved grades, exclusions and teacher actions distinct", () => {
    expect(pdfGrade({ ...sampleReport.results[0], outcome: "passed", level: { name: "Sufficient", ordinal: 3, points: 1.5, max_points: 2 } })).toBe("Sufficient");
    expect(pdfGrade({ ...sampleReport.results[0], outcome: "not_applicable" })).toBe("Excluded from readiness");
    expect(pdfFindingState(sampleFlags[0])).toBe("Open - needs teacher review");
    expect(pdfFindingState({ ...sampleFlags[0], overridden: true })).toBe("Dismissed by teacher");
    expect(pdfFindingState({ ...sampleFlags[0], overridden: true, confirmed_citation_source: true })).toBe("Source confirmed by teacher");
  });
  it("rejects another report before generating source pages", async () => {
    const input = inputs(); input.report.manuscript_id = 2;
    await expect(buildWorkspacePdf(input)).rejects.toThrow("does not match this report");
    expect(input.fetchAsset).not.toHaveBeenCalled();
  });
  it.each(["fake", "unknown", "real"] as const)("does not relabel %s model records as skipped", async mode => {
    const input = inputs(); input.report.llm_mode = mode;
    await expect(buildWorkspacePdf(input)).rejects.toThrow("review mode does not match");
    input.report.llm_mode = "manual"; input.flags[0].llm_mode = mode;
    await expect(buildWorkspacePdf(input)).rejects.toThrow("review mode does not match");
  });
  it("rejects mismatched criterion quotations even if the original file is unavailable", async () => {
    const input = inputs(); input.report.results[0].evidence[0].quote = "A different claim";
    await expect(buildWorkspacePdf(input)).rejects.toThrow("criterion's saved evidence");
  });
  it("rejects wrong-run and role-swapped source records even when the file is unavailable", async () => {
    const wrongRun = inputs(); wrongRun.flags[0].check_run_id = 2;
    await expect(buildWorkspacePdf(wrongRun)).rejects.toThrow("another report");
    const wrongRole = inputs(); wrongRole.flags[0].source_evidence![0].role = "different-role";
    await expect(buildWorkspacePdf(wrongRole)).rejects.toThrow("finding's saved evidence");
    const missing = inputs(); missing.flags[0].source_evidence = [];
    await expect(buildWorkspacePdf(missing)).rejects.toThrow("finding's saved evidence");
  });
  it("retains the text-only report when source images were deliberately removed", async () => {
    const input = inputs(); const blob = await buildWorkspacePdf(input);
    expect(blob.type).toBe("application/pdf"); expect(blob.size).toBeGreaterThan(1000);
    expect(input.fetchAsset).not.toHaveBeenCalled();
  });
  it("fails export when an original image cannot be loaded", async () => {
    const input = inputs(); input.fileAvailable = true;
    input.fetchAsset.mockResolvedValue(new Response("unavailable", { status: 404 }));
    await expect(buildWorkspacePdf(input)).rejects.toThrow("could not be loaded");
  });
  it("rejects image bytes that do not match the recorded checksum", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const input = inputs(); input.fileAvailable = true;
    input.fetchAsset.mockResolvedValue(new Response("corrupted image"));
    await expect(buildWorkspacePdf(input)).rejects.toThrow("checksum does not match");
  });
  it("does not invent a label for an unknown saved teacher decision", async () => {
    const input = inputs(); input.report.decision = "unexpected" as typeof input.report.decision;
    await expect(buildWorkspacePdf(input)).rejects.toThrow("decision is not recognized");
  });
});
