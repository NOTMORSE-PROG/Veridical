// F8.5/F8.10, screen 4s: describe the stored entry, never today's report.
import { CRITERION_EXCLUSION_COPY } from "../domain/criterionReview";
import { DECISION_LABEL } from "../domain/decisionTone";
import { READINESS_LABEL } from "../domain/readinessTone";

export interface RecordedPendingItem {
  criterionId: number;
  resultId: number;
  outcome: string;
}

export interface InstructorEventDetails {
  heading: string;
  fields: { label: string; value: string; role: "primary" | "reason" | "secondary" }[];
  notice?: string;
  pendingItems?: RecordedPendingItem[];
  pendingQualification?: string;
}

const PENDING_OUTCOMES: Readonly<Record<string, string>> = {
  escalated: "Needs instructor judgment",
  api_down: "Not graded: service unavailable",
  quota_exhausted: "Not graded: service allowance reached",
};

export function recordedId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function record(value: unknown): Record<string, unknown> | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return undefined;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null ? value as Record<string, unknown> : undefined;
}

function label(map: Readonly<Record<string, string>>, value: unknown): string | undefined {
  return typeof value === "string" && Object.hasOwn(map, value) ? map[value] : undefined;
}

function reason(payload: Record<string, unknown>, key: string, empty?: string): string {
  if (!Object.hasOwn(payload, key)) return "Unavailable in this entry.";
  const value = payload[key];
  if (typeof value === "string" && value.trim()) return value;
  if (empty && (value === null || typeof value === "string" && !value.trim())) return empty;
  return "Unavailable in this entry.";
}

function storedDate(value: unknown): string {
  if (typeof value !== "string"
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    || !Number.isFinite(Date.parse(value))) return "Unavailable in this entry.";
  const date = value.slice(0, 10);
  if (!new Date(`${date}T00:00:00Z`).toISOString().startsWith(date)) return "Unavailable in this entry.";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

function pendingSnapshot(payload: Record<string, unknown>) {
  if (!Object.hasOwn(payload, "pending_criteria")) {
    return { value: "Pending items were not recorded in this entry." };
  }
  if (!Array.isArray(payload.pending_criteria)) {
    return { value: "Pending items could not be summarized from this entry." };
  }
  if (payload.pending_criteria.length === 0) {
    return { value: "No pending items are listed in this entry.", items: [] };
  }
  const candidates = payload.pending_criteria.map(record);
  const criterionCounts = new Map<number, number>();
  const resultCounts = new Map<number, number>();
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (recordedId(candidate.criterion_id)) criterionCounts.set(candidate.criterion_id, (criterionCounts.get(candidate.criterion_id) ?? 0) + 1);
    if (recordedId(candidate.check_result_id)) resultCounts.set(candidate.check_result_id, (resultCounts.get(candidate.check_result_id) ?? 0) + 1);
  }
  const items: RecordedPendingItem[] = [];
  for (const candidate of candidates) {
    if (!candidate || !recordedId(candidate.criterion_id) || !recordedId(candidate.check_result_id)) continue;
    const outcome = label(PENDING_OUTCOMES, candidate.outcome);
    if (!outcome || criterionCounts.get(candidate.criterion_id) !== 1 || resultCounts.get(candidate.check_result_id) !== 1) continue;
    items.push({ criterionId: candidate.criterion_id, resultId: candidate.check_result_id, outcome });
  }
  const partial = items.length !== candidates.length;
  return {
    value: partial ? "The stored list could not be summarized completely."
      : `${items.length} recorded ${items.length === 1 ? "item" : "items"}.`,
    items,
    qualification: partial
      ? "Only readable, non-duplicated recorded items are shown. This is not a complete list."
      : "These are the items recorded in this entry, not a statement of the report's current unresolved criteria.",
  };
}

export function recordedActor(payload: unknown, readerId: unknown): string {
  const actor = record(payload)?.instructor_id;
  return recordedId(actor) ? recordedId(readerId) && actor === readerId ? "You" : `Instructor #${actor}`
    : "Instructor attribution unavailable";
}

export function instructorEventDetails(event: string, rawPayload: unknown): InstructorEventDetails | undefined {
  const payload = record(rawPayload) ?? {};
  if (event === "report_decided") {
    const pending = pendingSnapshot(payload);
    return {
      heading: "Instructor decision recorded",
      fields: [
        { role: "primary", label: "Recorded decision", value: label(DECISION_LABEL, payload.decision) ?? "Unavailable in this entry." },
        { role: "reason", label: "Decision reason", value: reason(payload, "note", "No decision note was recorded.") },
        { role: "secondary", label: "System readiness recorded with this decision", value: label(READINESS_LABEL, payload.status) ?? "Unavailable in this entry." },
        { role: "secondary", label: "Pending items recorded in this entry", value: pending.value },
      ],
      notice: payload.decision === "returned" ? "Returning did not grade or exclude criteria. This entry records that action; the current report may have changed since then." : undefined,
      pendingItems: pending.items,
      pendingQualification: pending.qualification,
    };
  }
  if (event === "report_reopened") {
    return {
      heading: "Decision reopened",
      fields: [
        { role: "reason", label: "Reopen reason", value: reason(payload, "reason") },
        { role: "secondary", label: "Previous recorded decision", value: label(DECISION_LABEL, payload.prior_decision) ?? "Unavailable in this entry." },
        { role: "secondary", label: "Previous decision stored time", value: storedDate(payload.prior_decided_at) },
        { role: "reason", label: "Previous decision reason", value: reason(payload, "prior_note", "No decision note was recorded.") },
      ],
      notice: "This entry records reopening the decision for further review. It does not record a new defense-readiness decision or establish the report's current state.",
    };
  }
  if (event !== "escalation_resolved") return undefined;
  let judgment: string;
  switch (payload.resolution) {
    case "mark_pass": judgment = "Meets criterion"; break;
    case "mark_fail": judgment = "Does not meet criterion"; break;
    case "needs_document": judgment = `${CRITERION_EXCLUSION_COPY.recorded}. ${CRITERION_EXCLUSION_COPY.noGrade}`; break;
    case "mark_level": judgment = "Named-level judgment recorded. The selected level name is unavailable in this entry."; break;
    case "accept_majority": judgment = "Accepted the recorded AI suggestion."; break;
    default: judgment = "The recorded judgment is unavailable in this entry.";
  }
  const fields: InstructorEventDetails["fields"] = [
    { role: "primary", label: "Criterion", value: recordedId(payload.criterion_id) ? `Criterion #${payload.criterion_id}` : "Unavailable in this entry." },
    { role: "primary", label: "Recorded judgment", value: judgment },
    { role: "reason", label: "Instructor reason", value: reason(payload, "reason") },
  ];
  if (recordedId(payload.check_result_id)) fields.push({ role: "secondary", label: "Recorded result identifier", value: `#${payload.check_result_id}` });
  if (payload.resolution === "accept_majority") {
    fields.push({ role: "secondary", label: "Recorded AI suggestion label", value: reason(payload, "ai_majority_verdict") });
  }
  return { heading: "Criterion judgment recorded", fields };
}
