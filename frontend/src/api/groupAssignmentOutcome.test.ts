import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { isKnownAssignmentRejection, validateManuscriptGroup, validateProposalOutcome, validateSavedGroup, validateSelectionOutcome, type GroupSelectionOutput, type SavedGroup } from "./groupAssignmentOutcome";

const saved: SavedGroup = { id: 7, name: "Practice team", member_names: ["Maria", "Ana"], program_id: 1, program: "IT", title: "Saved title", fingerprint: "a".repeat(64) };
const input = { saved_group_id: 7, fingerprint: saved.fingerprint, expected_group_id: 3 };
function response(): GroupSelectionOutput {
  return { action: "select_saved_group", manuscript_id: 9, outcome: "selected", receipt: {
    version: 2, manuscript_id: 9, instructor_id: 4, assigned_group_id: 7, request: { ...input }, effective: structuredClone(saved),
  } };
}
describe("saved group assignment receipts", () => {
  it("accepts a bound explicit selection and exact unchanged acknowledgement", () => {
    for (const outcome of ["selected", "unchanged"] as const) {
      const value = { ...response(), outcome };
      expect(validateSelectionOutcome(value, 9, 4, input, saved)).toBe(value);
    }
  });
  it.each([
    { action: "matched" }, { manuscript_id: 10 }, { outcome: "created" }, { extra: true }, { receipt: null },
  ])("rejects an unbound response envelope %j", (change) => {
    expect(() => validateSelectionOutcome({ ...response(), ...change }, 9, 4, input, saved)).toThrow();
  });
  it.each([
    { version: "2" }, { version: true }, { instructor_id: 5 }, { manuscript_id: 10 }, { assigned_group_id: 8 },
    { extra: "unreviewed" }, { request: { ...input, expected_group_id: null } },
    { request: { ...input, fingerprint: "b".repeat(64) } }, { request: { ...input, saved_group_id: "7" } },
    { request: { saved_group_id: 7, fingerprint: saved.fingerprint } },
  ])("rejects mismatched receipt %j", (change) => {
    const value = response();
    expect(() => validateSelectionOutcome({ ...value, receipt: { ...value.receipt, ...change } }, 9, 4, input, saved)).toThrow();
  });
  it.each([
    { id: 8 }, { name: "Practice team (2)" }, { member_names: ["Ana", "Maria"] },
    { program_id: null }, { program: "CS" }, { title: "New title" }, { fingerprint: "b".repeat(64) },
    { member_names: ["Maria", "Ana", "New member"] }, { extra: true },
  ])("never substitutes unreviewed effective facts %j", (change) => {
    const value = response();
    expect(() => validateSelectionOutcome({ ...value, receipt: { ...value.receipt, effective: { ...saved, ...change } } }, 9, 4, input, saved)).toThrow();
  });
  it("accepts null expected assignment only when it was explicitly sent", () => {
    const value = response();
    value.receipt.request.expected_group_id = null;
    expect(validateSelectionOutcome(value, 9, 4, { ...input, expected_group_id: null }, saved)).toBe(value);
  });
  it("ignores property order, retaining member order and actual display case", () => {
    const value = response();
    value.receipt.effective = Object.fromEntries(Object.entries(saved).reverse()) as unknown as SavedGroup;
    expect(validateSelectionOutcome(value, 9, 4, input, saved)).toBe(value);
  });
});
describe("saved group reads and assignment certainty", () => {
  it("uses actual current ID even when the real group is named Ungrouped", () => {
    const group = { id: 3, name: "Ungrouped", member_names: [], program_id: null, program: null, title: null };
    expect(validateManuscriptGroup({ manuscript_id: 9, group, confirmation: "not_recorded" }, 9).group?.id).toBe(3);
    expect(validateManuscriptGroup({ manuscript_id: 9, group: null, confirmation: "not_recorded" }, 9).group).toBeNull();
  });
  it.each([{ id: true }, { id: 8 }, { fingerprint: "A".repeat(64) }, { member_names: [1] }, { program_id: "1" }])("rejects malformed or wrong saved identity %j", (change) => {
    expect(() => validateSavedGroup({ ...saved, ...change }, 7)).toThrow();
  });
  it("does not equate network, server, malformed receipt or ambiguous error with a rejection", () => {
    for (const error of [new Error("Network"), new ApiError(500, "internal", "Failed"), new ApiError(409, "internal", "Failed")]) expect(isKnownAssignmentRejection(error)).toBe(false);
    expect(isKnownAssignmentRejection(new ApiError(409, "conflict", "Details changed"))).toBe(true);
  });
  it("keeps proposal outcomes distinct and refuses contradictory matched claims", () => {
    for (const outcome of ["created", "matched", "unchanged"] as const) expect(validateProposalOutcome({ group_id: 7, group_label: "Practice team", program: null, title: null, outcome, matched: outcome === "matched" }).outcome).toBe(outcome);
    expect(() => validateProposalOutcome({ group_id: 7, group_label: "Practice team", program: null, title: null, outcome: "created", matched: true })).toThrow();
  });
});
