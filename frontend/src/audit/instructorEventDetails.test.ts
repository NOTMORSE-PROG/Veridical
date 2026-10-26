import { describe, expect, it } from "vitest";
import { instructorEventDetails, recordedActor } from "./instructorEventDetails";
import { auditEventOrigin } from "./eventOrigin";

const SELECTION_ENTRY = { event_type: "manuscript_group_selected", manuscript_id: 9, check_run_id: null, created_at: "2026-10-26T02:00:00Z", group_selection: selectionPayload() };
function selectionPayload() {
  return { schema_version: 1, instructor_id: 4, manuscript_id: 9,
    before: { group_id: 3, group_label: "Ungrouped" },
    selection: { version: 2, manuscript_id: 9, instructor_id: 4, assigned_group_id: 7,
      request: { saved_group_id: 7, fingerprint: "a".repeat(64), expected_group_id: 3 },
      effective: { id: 7, name: "Saved Practice team", member_names: ["Maria", "Ana"], program_id: 1, program: "IT", title: "Recorded title", fingerprint: "a".repeat(64) } },
  };
}
describe("explicit saved assignment history", () => {
  it("shows exact stored facts and origin without claiming automated matching or current state", () => {
    const payload = selectionPayload();
    const before = structuredClone(payload);
    const detail = instructorEventDetails(SELECTION_ENTRY.event_type, payload, SELECTION_ENTRY)!;
    expect(auditEventOrigin(SELECTION_ENTRY.event_type)).toBe("instructor");
    expect(recordedActor(payload, 4, SELECTION_ENTRY)).toBe("You");
    expect(detail.fields).toContainEqual({ label: "Recorded saved group", value: "Saved Practice team · Group #7", role: "primary" });
    expect(detail.fields).toContainEqual({ label: "Saved group members", value: "Maria\nAna", role: "primary" });
    expect(detail.fields).toContainEqual({ label: "Previous assignment", value: "Ungrouped · Group #3", role: "secondary" });
    expect(detail.notice).toContain("at that time");
    expect(JSON.stringify(detail)).not.toContain("matched");
    expect(payload).toEqual(before);
  });
  it.each([
    { manuscript_id: 10 }, { schema_version: 2 }, { instructor_id: 5 }, { before: null },
    { before: { group_id: null, group_label: "Ungrouped" } },
    { selection: { ...selectionPayload().selection, assigned_group_id: 8 } },
    { selection: { ...selectionPayload().selection, effective: { ...selectionPayload().selection.effective, fingerprint: "b".repeat(64) } } },
  ])("withholds derived facts and actor for unbound receipt %j", change => {
    const payload = { ...selectionPayload(), ...change };
    const detail = instructorEventDetails(SELECTION_ENTRY.event_type, payload, SELECTION_ENTRY)!;
    expect(detail.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    expect(recordedActor(payload, 4, SELECTION_ENTRY)).toBe("Instructor attribution unavailable");
  });
  it("requires authoritative direct manuscript metadata and a direct event", () => {
    for (const entry of [{ ...SELECTION_ENTRY, manuscript_id: undefined }, { ...SELECTION_ENTRY, manuscript_id: 10 }, { ...SELECTION_ENTRY, check_run_id: 2 }, { ...SELECTION_ENTRY, group_selection: null }, { ...SELECTION_ENTRY, group_selection: undefined }]) {
      const detail = instructorEventDetails(SELECTION_ENTRY.event_type, selectionPayload(), entry)!;
      expect(detail.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    }
  });
  it("withholds changed stored facts when the validated historical projection retains the original checksum", () => {
    const payload = selectionPayload();
    payload.selection.effective.name = "UNSUPPORTED changed team";
    payload.selection.effective.member_names = ["UNSUPPORTED private member"];
    const detail = instructorEventDetails(SELECTION_ENTRY.event_type, payload, SELECTION_ENTRY)!;
    expect(detail.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    expect(recordedActor(payload, 4, SELECTION_ENTRY)).toBe("Instructor attribution unavailable");
  });
});

const REVISION_ENTRY = { event_type: "judgment_revised", check_run_id: 25, created_at: "2026-10-24T03:10:34.869928Z" };
function revisionPayload() {
  const after = { judgment_revision: 2, resolution_type: "mark_fail", outcome: "failed", score: 0, level: null,
    reason: "Revised full reason <not markup> & text.  ", actor_id: 5, recorded_at: REVISION_ENTRY.created_at, provenance: "recorded" };
  return { judgment: { schema_version: 1, action: "judgment_revised", check_run_id: 25, criterion_id: 40,
    check_result_id: 214, performed_by: 5, assessment_revision_before: 1, assessment_revision_after: 2,
    before: { ...after, judgment_revision: 1, reason: "Original full reason.", recorded_at: "2026-10-24T03:09:39.748868Z" }, after } };
}

function fields(event: string, payload: unknown) {
  return Object.fromEntries(instructorEventDetails(event, payload)!.fields.map(field => [field.label, field.value]));
}

const INITIAL_ENTRY = { ...REVISION_ENTRY, event_type: "judgment_recorded" };
function initialPayload(): { judgment: Record<string, unknown> } {
  const revised = revisionPayload().judgment;
  return { judgment: { ...revised, schema_version: 2, action: "judgment_recorded", before: null,
    assessment_revision_before: 0, assessment_revision_after: 1,
    after: { ...revised.after, judgment_revision: 1 },
    original_assessment: { kind: "semantic", original_outcome: "passed", original_score: 100, original_level: null } } };
}

describe("initial instructor action audit", () => {
  it("keeps the real automated band separate from the first manual judgment and full reason", () => {
    const payload = initialPayload();
    const before = structuredClone(payload);
    const result = instructorEventDetails("judgment_recorded", payload, INITIAL_ENTRY)!;
    expect(result.fields).toContainEqual({ role: "primary", label: "Original automated assessment", value: "Meets criterion" });
    expect(result.fields).toContainEqual({ role: "primary", label: "Instructor judgment", value: "Does not meet" });
    expect(result.fields).toContainEqual({ role: "reason", label: "Instructor reason", value: revisionPayload().judgment.after.reason });
    expect(result.fields.some(field => field.label.includes("Previous instructor"))).toBe(false);
    expect(recordedActor(payload, 5, INITIAL_ENTRY)).toBe("You");
    expect(result.notice).toContain("at that time");
    expect(payload).toEqual(before);
  });

  it.each([
    { schema_version: 1 }, { action: "initial_resolution" }, { before: {} }, { check_run_id: 26 },
    { criterion_id: "40" }, { performed_by: true }, { performed_by: 6 },
    { assessment_revision_after: 2 }, { assessment_revision_before: -1 }, { original_assessment: null },
    { original_assessment: { kind: "semantic", original_outcome: "passed", original_score: true, original_level: null } },
    { original_assessment: { kind: "semantic", original_outcome: "api_down", original_score: null, original_level: null } },
  ])("withholds attribution and all derived facts for invalid schema-2 envelope %j", change => {
    const payload = initialPayload(); Object.assign(payload.judgment, change);
    const result = instructorEventDetails("judgment_recorded", payload, INITIAL_ENTRY)!;
    expect(result.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    expect(recordedActor(payload, 5, INITIAL_ENTRY)).toBe("Instructor attribution unavailable");
  });

  it("does not turn an AI-majority action or inconsistent named grade into a new instructor record", () => {
    const payload = initialPayload();
    const after = payload.judgment.after as Record<string, unknown>;
    Object.assign(after, { resolution_type: "accept_majority" });
    expect(instructorEventDetails("judgment_recorded", payload, INITIAL_ENTRY)!.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    Object.assign(after, { resolution_type: "mark_level", outcome: "passed", score: 100,
      level: { ordinal: 0, name: "Absent", points: 0, max_points: 2 } });
    expect(recordedActor(payload, 5, INITIAL_ENTRY)).toBe("Instructor attribution unavailable");
    after.score = 0;
    expect(instructorEventDetails("judgment_recorded", payload, INITIAL_ENTRY)!.fields).toContainEqual({ role: "primary", label: "Instructor judgment", value: "Absent" });
  });

  it("binds actual event time and run without a legacy actor fallback", () => {
    const payload = initialPayload();
    expect(recordedActor(payload, 5, { ...INITIAL_ENTRY, created_at: "2026-10-24T03:10:34.869927Z" })).toBe("Instructor attribution unavailable");
    expect(recordedActor(payload, 5, { ...INITIAL_ENTRY, check_run_id: 99 })).toBe("Instructor attribution unavailable");
    expect(recordedActor({ ...payload, instructor_id: 6 }, 5, INITIAL_ENTRY)).toBe("Instructor attribution unavailable");
    expect(recordedActor({ instructor_id: 5 }, 5, INITIAL_ENTRY)).toBe("Instructor attribution unavailable");
  });
});

describe("stored instructor action presentation", () => {
  it("BUG-463: reads the bound performer and full before/after reasons without assuming a grade change", () => {
    const payload = revisionPayload();
    const before = structuredClone(payload);
    const result = instructorEventDetails("judgment_revised", payload, REVISION_ENTRY)!;
    expect(result.fields.filter(field => field.value === "Does not meet")).toHaveLength(2);
    expect(result.fields).toContainEqual({ role: "reason", label: "Revision reason", value: payload.judgment.after.reason });
    expect(result.fields).toContainEqual({ role: "reason", label: "Previous instructor reason", value: payload.judgment.before.reason });
    expect(recordedActor(payload, 5, REVISION_ENTRY)).toBe("You");
    expect(recordedActor(payload, 1, REVISION_ENTRY)).toBe("Instructor #5");
    expect(recordedActor(payload, undefined, REVISION_ENTRY)).toBe("Instructor #5");
    expect(payload).toEqual(before);
  });

  it.each([
    { schema_version: 2 }, { action: "initial_resolution" }, { check_run_id: 26 },
    { performed_by: true }, { performed_by: "5" }, { performed_by: 0 }, { performed_by: -1 },
    { performed_by: Number.MAX_SAFE_INTEGER + 1 }, { assessment_revision_after: 4 },
    { assessment_revision_before: -1 }, { criterion_id: "40" }, { check_result_id: null },
    { after: null }, { before: [] },
  ])("BUG-463: withholds every ordinary revision fact for inconsistent envelope %j", patch => {
    const payload = { judgment: { ...revisionPayload().judgment, ...patch } };
    const result = instructorEventDetails("judgment_revised", payload, REVISION_ENTRY)!;
    expect(result.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    expect(recordedActor(payload, 5, REVISION_ENTRY)).toBe("Instructor attribution unavailable");
    expect(result.notice).toContain("could not be summarized");
  });

  it.each([
    { actor_id: 6 }, { actor_id: undefined }, { actor_id: "5" }, { judgment_revision: 4 },
    { recorded_at: "2026-10-24T03:10:34.869929Z" }, { recorded_at: "2026-02-30T03:10:34.869928Z" },
    { recorded_at: "2026-10-24T03:10:34" }, { reason: "" }, { score: true },
    { level: { name: null } }, { level: { ordinal: 1, name: "Invented grade", points: 1, max_points: 1 } },
    { resolution_type: "mark_pass", score: 100 }, { provenance: "legacy_partial" },
  ])("BUG-463: safely rejects malformed or contradictory revised snapshot %j", patch => {
    const payload = revisionPayload();
    const raw = { judgment: { ...payload.judgment, after: { ...payload.judgment.after, ...patch } } };
    expect(instructorEventDetails("judgment_revised", raw, REVISION_ENTRY)!.fields.every(field => field.value === "Unavailable in this entry.")).toBe(true);
    expect(recordedActor(raw, 5, REVISION_ENTRY)).toBe("Instructor attribution unavailable");
  });

  it("BUG-463: accepts equivalent UTC offsets while retaining microsecond identity", () => {
    const payload = revisionPayload();
    payload.judgment.after.recorded_at = "2026-10-24T11:10:34.869928+08:00";
    expect(recordedActor(payload, 5, REVISION_ENTRY)).toBe("You");
    expect(recordedActor(payload, 5, { ...REVISION_ENTRY, created_at: "2026-10-24T03:10:34.869927Z" })).toBe("Instructor attribution unavailable");
  });

  it("BUG-463: refuses a conflicting legacy actor and inherited nested attributes without fallbacks", () => {
    const payload = revisionPayload();
    expect(recordedActor({ ...payload, instructor_id: 1 }, 5, REVISION_ENTRY)).toBe("Instructor attribution unavailable");
    expect(recordedActor({ ...payload, instructor_id: 5 }, 5, REVISION_ENTRY)).toBe("You");
    expect(recordedActor({ judgment: Object.create(payload.judgment) }, 5, REVISION_ENTRY)).toBe("Instructor attribution unavailable");
    expect(recordedActor(payload, 5, { ...REVISION_ENTRY, check_run_id: 26 })).toBe("Instructor attribution unavailable");
    expect(recordedActor({ instructor_id: 5 }, 5, REVISION_ENTRY)).toBe("Instructor attribution unavailable");
  });
  it("reads the actual returned outcome and reason without consulting a current report", () => {
    const payload = { decision: "returned", note: "Original full reason <not markup> & αβ", status: "needs_review",
      pending_criteria: [{ criterion_id: 35, check_result_id: 178, outcome: "escalated" }] };
    const before = structuredClone(payload);
    const result = fields("report_decided", payload);
    expect(result["Recorded decision"]).toBe("Returned for revision");
    expect(result["Decision reason"]).toBe(payload.note);
    expect(result["Pending items recorded in this entry"]).toBe("1 recorded item.");
    expect(payload).toEqual(before);
  });

  it.each([null, "", " \n "])("distinguishes an explicit empty note from missing or malformed (%s)", value => {
    expect(fields("report_decided", { note: value })["Decision reason"]).toBe("No decision note was recorded.");
    expect(fields("report_decided", {})["Decision reason"]).toBe("Unavailable in this entry.");
    expect(fields("report_decided", { note: {} })["Decision reason"]).toBe("Unavailable in this entry.");
  });

  it("does not turn absent, malformed or partially readable pending snapshots into a complete zero", () => {
    expect(fields("report_decided", {})["Pending items recorded in this entry"]).toContain("not recorded");
    expect(fields("report_decided", { pending_criteria: [] })["Pending items recorded in this entry"]).toBe("No pending items are listed in this entry.");
    expect(fields("report_decided", { pending_criteria: "[]" })["Pending items recorded in this entry"]).toContain("could not be summarized");
    const result = instructorEventDetails("report_decided", { pending_criteria: [
      { criterion_id: 35, check_result_id: 178, outcome: "escalated" },
      { criterion_id: 36, check_result_id: 179, outcome: "passed" }, null,
    ] })!;
    expect(result.pendingItems).toHaveLength(1);
    expect(result.pendingQualification).toContain("not a complete list");
    expect(result.fields.at(-1)!.value).toContain("could not be summarized completely");
  });

  it.each(["criterion_id", "check_result_id"])("does not choose one contradictory duplicate %s as the history", field => {
    const first = { criterion_id: 35, check_result_id: 178, outcome: "escalated" };
    const second = { criterion_id: 36, check_result_id: 179, outcome: "api_down", [field]: first[field as "criterion_id" | "check_result_id"] };
    const result = instructorEventDetails("report_decided", { pending_criteria: [first, second] })!;
    expect(result.pendingItems).toEqual([]);
    expect(result.pendingQualification).toContain("not a complete list");
  });

  it("labels shape-valid identifiers as recorded without pretending to verify their run membership", () => {
    const result = instructorEventDetails("report_decided", { pending_criteria: [{ criterion_id: 999, check_result_id: 888, outcome: "quota_exhausted" }] })!;
    expect(result.pendingItems![0]).toEqual({ criterionId: 999, resultId: 888, outcome: "Not graded: service allowance reached" });
    expect(JSON.stringify(result)).not.toMatch(/verified|complete count|all pending/i);
  });

  it("retains an unverifiable criterion recorded by the integrated Return action without inventing its cause", () => {
    const payload = { decision: "returned", status: "needs_review", note: "Inspect the missing source evidence.",
      pending_criteria: [{ criterion_id: 42, check_result_id: 291, outcome: "unverifiable" }] };
    const before = structuredClone(payload);
    const result = instructorEventDetails("report_decided", payload)!;
    expect(result.pendingItems).toEqual([{ criterionId: 42, resultId: 291, outcome: "Unverifiable" }]);
    expect(result.fields.at(-1)!.value).toBe("1 recorded item.");
    expect(result.pendingQualification).toBe("These are the items recorded in this entry, not a statement of the report's current unresolved criteria.");
    expect(JSON.stringify(result.pendingItems)).not.toMatch(/AI|service|not assessed|not graded/i);
    expect(payload).toEqual(before);
  });

  it("describes the actual prior decision without asserting the report is still reopened now", () => {
    const result = instructorEventDetails("report_reopened", { reason: "Read the source again.", prior_decision: "returned", prior_note: "Original return reason.", prior_decided_at: "2026-10-23T01:02:03+00:00" })!;
    expect(result.fields).toContainEqual({ role: "secondary", label: "Previous recorded decision", value: "Returned for revision" });
    expect(result.fields).toContainEqual({ role: "reason", label: "Reopen reason", value: "Read the source again." });
    expect(result.notice).toContain("does not record a new");
    expect(result.fields.at(-1)!.value).not.toContain("Unavailable");
  });

  it.each(["2026-02-30T01:02:03Z", "2026-10-23", "2026-10-23T01:02:03", {}, null])("does not invent a prior stored time for malformed %s", value => {
    expect(fields("report_reopened", { prior_decided_at: value })["Previous decision stored time"]).toBe("Unavailable in this entry.");
  });

  it("does not reconstruct the missing selected level from score or AI suggestion", () => {
    const result = fields("escalation_resolved", { resolution: "mark_level", criterion_id: 35, check_result_id: 178, new_score: 50, new_outcome: "passed", ai_majority_verdict: "Proficient", reason: "The actual sandbox reason." });
    expect(result["Recorded judgment"]).toContain("selected level name is unavailable");
    expect(JSON.stringify(result)).not.toMatch(/Satisfactory|Proficient|50/);
    expect(result["Instructor reason"]).toBe("The actual sandbox reason.");
  });

  it("keeps a recorded exclusion ungraded and an accepted suggestion explicitly attributed", () => {
    expect(fields("escalation_resolved", { resolution: "needs_document" })["Recorded judgment"]).toContain("No grade recorded.");
    expect(fields("escalation_resolved", { resolution: "accept_majority", ai_majority_verdict: "Recorded named level" })["Recorded AI suggestion label"]).toBe("Recorded named level");
  });

  it.each([true, "35", 0, -1, Number.MAX_SAFE_INTEGER + 1])("rejects malformed identity %s without coercion", value => {
    expect(recordedActor({ instructor_id: value }, 1)).toBe("Instructor attribution unavailable");
    expect(fields("escalation_resolved", { criterion_id: value })["Criterion"]).toBe("Unavailable in this entry.");
  });

  it("attributes You only to the actual valid matching reader", () => {
    expect(recordedActor({ instructor_id: 1 }, 1)).toBe("You");
    expect(recordedActor({ instructor_id: 2 }, 1)).toBe("Instructor #2");
    expect(recordedActor({ instructor_id: 1 }, undefined)).toBe("Instructor #1");
    expect(recordedActor({}, 1)).toBe("Instructor attribution unavailable");
  });

  it("does not interpret inherited identifiers or unknown events as a known decision", () => {
    expect(fields("report_decided", { decision: "toString", status: "__proto__" })["Recorded decision"]).toBe("Unavailable in this entry.");
    expect(instructorEventDetails("llm_call", { verdict: "pass" })).toBeUndefined();
  });
});
