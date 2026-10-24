// F8.5/F8.10, screen 4s: describe the stored entry, never today's report.
import { CRITERION_EXCLUSION_COPY } from "../domain/criterionReview";
import { DECISION_LABEL } from "../domain/decisionTone";
import { READINESS_LABEL } from "../domain/readinessTone";
import type { AuditLogDetail, CriterionLevel } from "../api/types";
import type { JudgmentSnapshot } from "../api/judgments";
import { judgmentLabel, snapshotValid } from "../report/judgmentRecord";
import { auditEventOrigin } from "./eventOrigin";

type EntryBinding = Pick<AuditLogDetail, "event_type" | "check_run_id" | "created_at">;

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
  unverifiable: "Unverifiable",
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

function ownValues(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return keys.every(key => Object.hasOwn(value, key));
}

function snapshot(raw: unknown): JudgmentSnapshot | undefined {
  const value = record(raw);
  if (!value || !ownValues(value, ["judgment_revision", "resolution_type", "outcome", "score", "level", "reason", "actor_id", "recorded_at", "provenance"])
    || !Number.isSafeInteger(value.judgment_revision) || (value.judgment_revision as number) < 0
    || typeof value.resolution_type !== "string" || !["mark_pass", "mark_fail", "mark_level", "needs_document", "accept_majority"].includes(value.resolution_type)
    || typeof value.outcome !== "string" || !["passed", "failed", "not_applicable"].includes(value.outcome)
    || !(value.score === null || typeof value.score === "number" && Number.isFinite(value.score))
    || typeof value.reason !== "string" || !value.reason.trim()
    || !(value.actor_id === null || recordedId(value.actor_id))
    || !(value.recorded_at === null || typeof value.recorded_at === "string" && timestampIdentity(value.recorded_at) !== undefined)
    || !["recorded", "legacy_partial"].includes(value.provenance as string)) return;
  let level: CriterionLevel | null = null;
  if (value.level !== null) {
    const candidate = record(value.level);
    if (!candidate || !ownValues(candidate, ["ordinal", "name", "points", "max_points"])
      || !Number.isSafeInteger(candidate.ordinal) || typeof candidate.name !== "string" || !candidate.name.trim()
      || typeof candidate.points !== "number" || !Number.isFinite(candidate.points)
      || typeof candidate.max_points !== "number" || !Number.isFinite(candidate.max_points)) return;
    level = { ordinal: candidate.ordinal as number, name: candidate.name, points: candidate.points, max_points: candidate.max_points };
    if (value.resolution_type !== "mark_level" && value.resolution_type !== "accept_majority") return;
  }
  // All nested types are guarded before invoking the typed domain validator.
  const decoded = { ...value, level } as unknown as JudgmentSnapshot;
  return snapshotValid(decoded) ? decoded : undefined;
}

function timestampIdentity(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return;
  const local = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(local) || new Date(local).toISOString().slice(0, 19) !== match[1]) return;
  const base = Date.parse(`${match[1]}${match[3]}`);
  if (!Number.isFinite(base)) return;
  return `${base}:${(match[2] ?? "").padEnd(9, "0")}`;
}

function revision(raw: unknown, entry: EntryBinding | undefined) {
  const payload = record(raw);
  const body = payload && Object.hasOwn(payload, "judgment") ? record(payload.judgment) : undefined;
  if (!entry || entry.event_type !== "judgment_revised" || !body
    || !ownValues(body, ["schema_version", "action", "check_run_id", "criterion_id", "check_result_id", "performed_by", "before", "after", "assessment_revision_before", "assessment_revision_after"])
    || body.schema_version !== 1 || body.action !== "judgment_revised"
    || !recordedId(body.check_run_id) || body.check_run_id !== entry.check_run_id
    || !recordedId(body.criterion_id) || !recordedId(body.check_result_id) || !recordedId(body.performed_by)
    || !Number.isSafeInteger(body.assessment_revision_before) || (body.assessment_revision_before as number) < 0
    || body.assessment_revision_after !== (body.assessment_revision_before as number) + 1
    || !Number.isSafeInteger(body.assessment_revision_after)) return;
  const before = snapshot(body.before);
  const after = snapshot(body.after);
  if (!before || !after || after.provenance !== "recorded" || after.actor_id !== body.performed_by
    || after.judgment_revision !== before.judgment_revision + 1
    || timestampIdentity(after.recorded_at) !== timestampIdentity(entry.created_at)
    || timestampIdentity(entry.created_at) === undefined) return;
  if (payload && Object.hasOwn(payload, "instructor_id") && payload.instructor_id !== body.performed_by) return;
  return { criterionId: body.criterion_id, resultId: body.check_result_id, actor: body.performed_by, before, after };
}

export function recordedActor(payload: unknown, readerId: unknown, entry?: EntryBinding): string {
  const legacy = record(payload);
  const actor = entry?.event_type === "judgment_revised" ? revision(payload, entry)?.actor
    : (!entry || auditEventOrigin(entry.event_type) === "instructor") && legacy && Object.hasOwn(legacy, "instructor_id") ? legacy.instructor_id : undefined;
  return recordedId(actor) ? recordedId(readerId) && actor === readerId ? "You" : `Instructor #${actor}`
    : "Instructor attribution unavailable";
}

export function instructorEventDetails(event: string, rawPayload: unknown, entry?: EntryBinding): InstructorEventDetails | undefined {
  const payload = record(rawPayload) ?? {};
  if (event === "judgment_revised") {
    const saved = revision(rawPayload, entry);
    return {
      heading: "Criterion judgment revised",
      fields: [
        { role: "primary", label: "Criterion", value: saved ? `Criterion #${saved.criterionId}` : "Unavailable in this entry." },
        { role: "primary", label: "Previous recorded judgment", value: saved ? judgmentLabel(saved.before) : "Unavailable in this entry." },
        { role: "primary", label: "Revised judgment", value: saved ? judgmentLabel(saved.after) : "Unavailable in this entry." },
        { role: "reason", label: "Revision reason", value: saved?.after.reason ?? "Unavailable in this entry." },
        { role: "reason", label: "Previous instructor reason", value: saved?.before.reason ?? "Unavailable in this entry." },
        { role: "secondary", label: "Recorded result identifier", value: saved ? `#${saved.resultId}` : "Unavailable in this entry." },
      ],
      notice: saved ? "This entry records a revision at that time. The current report may contain later judgments or decisions."
        : "Some recorded judgment details could not be summarized. The stored record is available below.",
    };
  }
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
