import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { isKnownUploadRejection, isUploadFileError, validateIngestReceipt, validateRubricReceipt } from "./uploadOutcome";

import { ingestReceipt, rubricReceipt } from "../test/uploadReceipts";

const criterion = { id: 3, type: "semantic", text: "Explain the method", evidence: null, weight: 10, position: 0 };

describe("upload receipt evidence", () => {
  it("accepts paragraph zero-page/partial proposals and genuine empty reviewable drafts", () => {
    expect(validateIngestReceipt(ingestReceipt)).toBe(ingestReceipt);
    expect(validateIngestReceipt({ ...ingestReceipt, group_proposal: {
      ...ingestReceipt.group_proposal, title: { value: "Partial title", anchor: "paragraph 1" },
    } }).group_proposal.members).toEqual([]);
    expect(validateRubricReceipt(rubricReceipt)).toBe(rubricReceipt);
  });
  it.each([
    { manuscript_id: "42" }, { manuscript_id: true }, { manuscript_id: Number.MAX_SAFE_INTEGER + 1 },
    { anchor_kind: ["page"] }, { vision_status: ["none"] }, { ingest_status: "processing" },
    { notes: [null] }, { text_chars: -1 }, { citations: 0.5 }, { group_proposal: null },
    { group_proposal: { ...ingestReceipt.group_proposal, members: [null] } },
    { group_proposal: { ...ingestReceipt.group_proposal, title: { value: "Title", anchor: "" } } },
    { existing_upload: { manuscript_id: 2, created_at: "2026-02-30T00:00:00Z", original_filename: null, purged_at: null } },
    { existing_upload: { manuscript_id: 2, created_at: "2026-10-23", original_filename: null, purged_at: null } },
  ])("rejects an unusable ingest receipt before exposing identity: %j", (change) => {
    expect(() => validateIngestReceipt({ ...ingestReceipt, ...change })).toThrow("could not confirm");
  });
  it.each([
    { id: "7" }, { rubric_family_id: "made-up" }, { version: 0 }, { parse_status: ["parsed"] },
    { parse_issues: "issue" }, { criteria: [null] }, { criteria: [criterion, criterion] },
    { criteria: [{ ...criterion, weight: 0 }] }, { criteria: [{ ...criterion, weight: Infinity }] },
    { criteria: [{ ...criterion, text: " " }] }, { criteria: [{ ...criterion, levels: [] }] },
    { criteria: [{ ...criterion, levels: [{ level: 1, name: "Low", descriptor: "Basic", points: 0 }] }] },
    { criteria: [{ ...criterion, levels: [
      { level: 2, name: "Low", descriptor: "Basic", points: 0 },
      { level: 1, name: "High", descriptor: "Detailed", points: 2 },
    ] }] },
  ])("rejects an unusable rubric receipt: %j", (change) => {
    expect(() => validateRubricReceipt({ ...rubricReceipt, ...change })).toThrow("could not confirm");
  });
  it("requires the submitted family and permits source-defined noncontiguous scales", () => {
    expect(() => validateRubricReceipt(rubricReceipt, "00000000-0000-4000-8000-000000000008")).toThrow();
    expect(validateRubricReceipt({ ...rubricReceipt, criteria: [{ ...criterion, levels: [
      { level: 2, name: "Basic", descriptor: "Basic method", points: 0 },
      { level: 8, name: "Detailed", descriptor: "Detailed method", points: 30 },
    ] }] }).criteria).toHaveLength(1);
  });
  it.each([
    [503, "document_preparation_busy", true, true], [503, "api_down", false, false],
    [429, "rate_limited", true, true], [429, "quota_exhausted", false, false],
    [413, "too_large", false, true], [422, "file_malformed", false, true],
    [422, "invalid_api_key", false, false], [404, "not_found", false, false],
  ])("keeps endpoint-specific cause separate from creation certainty: %s/%s", (status, code, manuscript, format) => {
    const error = new ApiError(status as number, code as string, "Specific safe cause");
    expect(isKnownUploadRejection(error, "manuscript")).toBe(manuscript);
    expect(isKnownUploadRejection(error, "format")).toBe(format);
    expect(isUploadFileError(error)).toBe(code === "too_large" || code === "file_malformed");
  });
});
