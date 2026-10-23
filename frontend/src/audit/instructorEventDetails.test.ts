import { describe, expect, it } from "vitest";
import { instructorEventDetails, recordedActor } from "./instructorEventDetails";

function fields(event: string, payload: unknown) {
  return Object.fromEntries(instructorEventDetails(event, payload)!.fields.map(field => [field.label, field.value]));
}

describe("stored instructor action presentation", () => {
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
