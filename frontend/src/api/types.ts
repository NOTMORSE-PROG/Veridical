// API response types — defined once, imported everywhere (CODING.md §3).
// Mirrors the backend pydantic schemas; keep in sync by hand (no codegen
// yet — small enough surface that a mismatch shows up immediately in the
// gallery/Playwright checks).

export interface Instructor {
  id: number;
  email: string;
  display_name: string;
}

export type CriterionType = "structural" | "semantic";

export interface Criterion {
  id: number;
  type: CriterionType;
  text: string;
  evidence: string | null;
  weight: number;
  position: number;
}

export interface Rubric {
  id: number;
  rubric_family_id: string;
  version: number;
  title: string;
  parse_status: "parsed" | "needs_review";
  parse_issues: string[] | null;
  is_active: boolean;
  is_latest_version: boolean;
  criteria: Criterion[];
}

export interface RubricListItem {
  id: number;
  rubric_family_id: string;
  version: number;
  title: string;
  is_active: boolean;
  created_at: string;
  criteria_count: number;
  report_count: number;
}

export interface ManuscriptListItem {
  id: number;
  group_label: string;
  ingest_status: "pending" | "processing" | "done" | "failed";
  created_at: string;
  latest_check_run_id: number | null;
  latest_check_run_status: CheckRunStatus | null;
}

export interface PaginatedManuscripts {
  items: ManuscriptListItem[];
  total: number;
  page: number;
  page_size: number;
}

export type CheckRunStatus =
  | "queued"
  | "ingesting"
  | "structural"
  | "semantic"
  | "integrity"
  | "aggregating"
  | "done"
  | "failed";

export interface StageEntry {
  status: string;
  [key: string]: unknown;
}

export interface StageStatus {
  stages?: Record<string, StageEntry>;
  blocked?: { code: string; message: string; resume_at: string | null };
  failed?: { code: string; message: string };
}

export interface CheckRun {
  id: number;
  manuscript_id: number;
  rubric_id: number;
  status: CheckRunStatus;
  stage_status: StageStatus | null;
  queue_position: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface EvidenceItem {
  quote: string;
  anchor: string;
}

export interface CriterionResultOut {
  criterion_id: number;
  text: string;
  type: CriterionType;
  weight: number;
  kind: string;
  outcome:
    | "passed"
    | "failed"
    | "escalated"
    | "not_applicable"
    | "unverifiable"
    | "api_down"
    | "quota_exhausted";
  score: number | null;
  basis: string | null;
  anchor: string | null;
  reasoning: string | null;
  reason: string | null;
  evidence: EvidenceItem[];
}

export interface ReportOut {
  check_run_id: number;
  manuscript_group_label: string;
  rubric_title: string;
  status: "ready" | "conditionally_ready" | "not_ready" | "needs_review";
  composite_score: number | null;
  thresholds: { ready_min_score: number; not_ready_max_score: number };
  reason: string | null;
  results: CriterionResultOut[];
}

export interface AuditLogSummary {
  id: number;
  event_type: string;
  check_run_id: number | null;
  manuscript_group_label: string | null;
  prompt_type: string | null;
  prompt_version: string | null;
  agreement_score: number | null;
  created_at: string;
}

export interface AuditLogDetail extends AuditLogSummary {
  input_hash: string | null;
  payload: Record<string, unknown>;
}

export interface PaginatedAuditLog {
  items: AuditLogSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface EscalatedItemOut {
  check_result_id: number;
  criterion_id: number;
  criterion_text: string;
  weight: number;
  agreement: number | null;
  votes: (string | null)[];
  ai_majority_verdict: string | null;
  reason: string | null;
}

export type EscalationResolution = "accept_majority" | "mark_pass" | "mark_fail";

export interface ResolveEscalationIn {
  resolution: EscalationResolution;
  reason: string;
}

export interface ResolveEscalationOut {
  check_result_id: number;
  outcome: string;
  score: number | null;
  report: ReportOut;
}

export interface FlagOut {
  id: number;
  check_result_id: number;
  check_kind: string;
  criterion_text: string | null;
  severity: "high" | "med" | "low";
  confidence: number | null;
  evidence_excerpt: string;
  page_anchor: string;
  annotation: string | null;
  overridden: boolean;
  override_reason: string | null;
  ai_verdict_summary: string | null;
  ai_reasoning: string | null;
}

export interface OverrideFlagOut extends FlagOut {
  report: ReportOut;
}

/** One model's own daily allowance. The Gemini free tier meters per model,
 * so the quota meter's totals are sums over these islands (V-049). */
export interface ModelQuotaStatus {
  model: string;
  calls_used: number;
  daily_limit: number;
  calls_remaining: number;
  cache_hits_today: number;
  rpm_limit: number;
  vision: boolean;
  exhausted: boolean;
}

export interface QuotaStatus {
  mode: "fake" | "live";
  quota_day: string;
  /** Aggregated across the whole model pool. */
  calls_used: number;
  daily_limit: number;
  calls_remaining: number;
  cache_hits_today: number;
  cache_hit_rate: number;
  reset_at: string;
  rpm_limit: number;
  models?: ModelQuotaStatus[];
}
