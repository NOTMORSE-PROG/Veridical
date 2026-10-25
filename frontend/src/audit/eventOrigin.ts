// F8.10, screen4s: event producers establish origin; ownership does not.
export type AuditEventOrigin = "instructor" | "automated" | "unknown";

const INSTRUCTOR_EVENTS: ReadonlySet<string> = new Set([
  "flag_overridden", "flag_annotated", "escalation_resolved", "judgment_revised", "judgment_recorded",
  "report_decided", "report_reopened", "manuscript_purged", "check_run_cancel_requested",
  "citation_source_confirmed", "manuscript_ingestion_failure_dismissed", "library_entry_edited",
]);
const AUTOMATED_EVENTS: ReadonlySet<string> = new Set([
  "llm_call", "llm_cache_hit", "llm_call_failed", "llm_model_exhausted", "llm_transport_attempt",
  "criterion_routing", "rubric_parse_attempt", "verdict_computed", "check_run_cancelled",
  "manuscript_withdrawn_from_corpus", "internal_agreement_check_computed",
  "citation_integrity_check_computed", "statistical_forensics_check_computed",
  "originality_reuse_check_computed", "library_entry_added", "library_entry_failed",
  "library_entry_needs_decision",
]);

export function auditEventOrigin(eventType: string): AuditEventOrigin {
  if (INSTRUCTOR_EVENTS.has(eventType)) return "instructor";
  if (AUTOMATED_EVENTS.has(eventType)) return "automated";
  // Library removal has both instructor and maintenance producers. List
  // summaries carry no actor payload; unknown events are equally unproven.
  return "unknown";
}
