import { describe, expect, it } from "vitest";
import type { RubricIssueDetail, RubricLevel } from "../api/types";
import {
  columnLevelName, formatReviewLabel, formatReviewState, keepNoteRequired, keepReasonLabel, keepReasonsFor, noteHasContent,
  orderedIssues, reasonKey,
} from "./rubricReview";

const issue = (status: RubricIssueDetail["status"], id = 1): RubricIssueDetail => ({
  id, criterion_id: 2, level: 3, code: "not_in_source", message: "m", status, resolution: null,
});

describe("format review state (labelling contract)", () => {
  it("counts only open items, whatever parse_status says", () => {
    const state = formatReviewState({ is_active: false, parse_status: "parsed", parse_issue_details: [issue("open"), issue("acknowledged", 2), issue("open", 3)] });
    expect(state).toEqual({ kind: "to_check", open: 2 });
    expect(formatReviewLabel(state)).toBe("2 items to check");
  });

  it("calls a flagged format with nothing open flagged when prepared, never needs review", () => {
    for (const is_active of [false, true]) {
      const state = formatReviewState({ is_active, parse_status: "needs_review", parse_issue_details: [issue("acknowledged"), issue("resolved", 2)] });
      expect(formatReviewLabel(state)).toBe("Flagged when prepared · all items checked");
    }
  });

  it("labels an active format with open items as the exception to correct in a new version", () => {
    const state = formatReviewState({ is_active: true, parse_status: "needs_review", parse_issue_details: [issue("open")] });
    expect(formatReviewLabel(state)).toBe("Active with items to check · correct it in a new version");
  });

  it("gives formats prepared before items were recorded no live label", () => {
    expect(formatReviewState({ is_active: true, parse_status: "needs_review", parse_issue_details: null })).toEqual({ kind: "legacy" });
    expect(formatReviewLabel({ kind: "legacy" })).toBeNull();
    expect(formatReviewLabel(formatReviewState({ is_active: false, parse_status: "parsed", parse_issue_details: [] }))).toBe("All level descriptions match the document");
  });

  it("tells a clean upload, which also has no items, from an older format by its traced levels", () => {
    const traced = { level: 1, name: "Beginner", descriptor: "d", points: 1, source: { match: "exact" as const, page: 1, table: 1, row: 2, col: 2, cell_text: "d", row_label: "Purpose", column_heading: "BEGINNER 1" } };
    const criterion = { id: 1, type: "semantic" as const, text: "Purpose", evidence: null, weight: 1, position: 0 };
    expect(formatReviewState({ is_active: false, parse_status: "parsed", parse_issue_details: null, criteria: [{ ...criterion, levels: [traced] }] })).toEqual({ kind: "all_match" });
    expect(formatReviewState({ is_active: false, parse_status: "parsed", parse_issue_details: null, criteria: [{ ...criterion, levels: [{ ...traced, source: undefined }] }] })).toEqual({ kind: "legacy" });
  });
});

describe("keeping wording", () => {
  it("never offers 'The document says it this way' for wording that was not found", () => {
    expect(keepReasonsFor(["not_in_source"]).map((reason) => reason.value)).toEqual(["outside_table", "other"]);
    expect(keepReasonsFor(["wrong_column"]).map((reason) => reason.value)).toEqual(["document_wording", "other"]);
    // Issues kept together share only the reasons that fit all of them.
    expect(keepReasonsFor(["not_in_source", "wrong_column"]).map((reason) => reason.value)).toEqual(["other"]);
    expect(keepReasonsFor(["level_left_out"]).map((reason) => reason.value)).toEqual(["document_wording", "outside_table", "other"]);
  });

  it("records a kept reason with the label it was offered under", () => {
    expect(keepReasonLabel("outside_table", "not_in_source")).toBe("It is in the document outside the table, for example in a note under it");
    expect(keepReasonLabel("outside_table")).toBe("The wording comes from outside the table");
    expect(keepReasonLabel(null)).toBeNull();
  });

  it("offers a whole-format warning its own reasons and needs a note for it", () => {
    const formatIssue = { criterion_id: null, code: "other" };
    expect(reasonKey(formatIssue)).toBe("whole_format");
    expect(reasonKey({ criterion_id: 2, code: "other" })).toBe("other");
    expect(keepReasonsFor([reasonKey(formatIssue)]).map((reason) => reason.value)).toEqual(["compared_whole_format", "other"]);
    expect(keepReasonLabel("compared_whole_format", reasonKey(formatIssue))).toBe("I compared the criteria with the original, and nothing in it is missing");
    expect(keepNoteRequired([reasonKey(formatIssue)])).toBe(true);
  });

  it("orders the whole-format items first, then criterion by criterion", () => {
    const at = (id: number, criterion_id: number | null, level: number | null): RubricIssueDetail => ({ ...issue("open", id), criterion_id, level });
    expect(orderedIssues([at(1, 7, 2), at(2, null, null), at(3, 6, 1), at(4, 99, 1)], [6, 7]).map((item) => item.id)).toEqual([2, 3, 1, 4]);
  });

  it("needs a note for untraced wording, and a note must say something", () => {
    expect(keepNoteRequired(["wrong_row"])).toBe(true);
    expect(keepNoteRequired(["level_left_out", "other"])).toBe(false);
    expect(noteHasContent("x")).toBe(false);
    expect(noteHasContent("..... !!!! ????")).toBe(false);
    expect(noteHasContent("Same as page 1")).toBe(false);
    expect(noteHasContent("Same as page 1, row 3")).toBe(true);
  });

  it("finds the level a misplaced column belongs to from its heading", () => {
    const levels: RubricLevel[] = [
      { level: 2, name: "Acceptable", descriptor: "d", points: 2, source: { match: "exact", page: 1, table: 1, row: 3, col: 4, cell_text: "c", row_label: "Purpose", column_heading: "PROFICIEN T 3" } },
      { level: 3, name: "Proficient", descriptor: "d", points: 3 },
    ];
    expect(columnLevelName(levels[0], levels)).toBe("Proficient");
    expect(columnLevelName({ ...levels[0], source: { ...levels[0].source!, column_heading: "Column C" } }, levels)).toBe("Column C");
  });
});
