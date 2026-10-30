// F8.10: format context is separate from the facts recorded in an Audit entry.
import type { AuditCriterionContext, AuditLogDetail } from "../api/types";
import { recordedId } from "./instructorEventDetails";

const JUDGMENT_EVENTS = new Set(["escalation_resolved", "judgment_recorded", "judgment_revised"]);
const BINDING_FIELDS = ["check_run_id", "criterion_id", "check_result_id"] as const;

export function hasCriterionContext(entry: AuditLogDetail): boolean {
  return JUDGMENT_EVENTS.has(entry.event_type);
}

export function boundCriterionContext(entry: AuditLogDetail): AuditCriterionContext | undefined {
  const context = entry.criterion_context;
  if (!hasCriterionContext(entry) || !context || !recordedId(entry.check_run_id)
    || context.check_run_id !== entry.check_run_id
    || !BINDING_FIELDS.every(key => recordedId(context[key]))
    || !recordedId(context.rubric_id) || !recordedId(context.rubric_version)
    || !Number.isSafeInteger(context.criterion_position) || context.criterion_position < 0
    || typeof context.criterion_text !== "string" || !context.criterion_text.trim()
    || typeof context.rubric_title !== "string") return;
  const payload = entry.payload;
  const body = Object.hasOwn(payload, "judgment") ? payload.judgment
    : entry.event_type === "escalation_resolved" ? payload : undefined;
  if (!body || typeof body !== "object" || Array.isArray(body)) return;
  const saved = body as Record<string, unknown>;
  if (saved.criterion_id !== context.criterion_id || saved.check_result_id !== context.check_result_id
    || (Object.hasOwn(payload, "judgment") && saved.check_run_id !== context.check_run_id)
    || BINDING_FIELDS.some(key => Object.hasOwn(payload, key) && payload[key] !== context[key])) return;
  return context;
}
