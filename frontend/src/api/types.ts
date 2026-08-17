// API response types — defined once, imported everywhere (CODING.md §3).
// Mirrors the backend pydantic schemas; keep in sync by hand (no codegen
// yet — small enough surface that a mismatch shows up immediately in the
// gallery/Playwright checks).

export interface Instructor {
  id: number;
  email: string;
  display_name: string;
  onboarding_dismissed_at: string | null;
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

export type IngestFailureReason = "file_too_large" | "unreadable_format" | "extraction_failed";

export interface ManuscriptListItem {
  id: number;
  group_label: string;
  // BUG-022: group_label defaults to "Ungrouped" and can't distinguish
  // two manuscripts alone; null for rows ingested before this field
  // existed.
  original_filename: string | null;
  ingest_status: "pending" | "processing" | "done" | "failed";
  ingest_failure_reason: IngestFailureReason | null;
  created_at: string;
  latest_check_run_id: number | null;
  latest_check_run_status: CheckRunStatus | null;
  // The latest DONE run specifically -- can differ from
  // latest_check_run_id when a newer re-run failed or is still running,
  // so a valid prior report never goes unreachable (backend-critic
  // finding on BUG-012, V-055).
  latest_done_check_run_id: number | null;
  // V-038: the latest DONE run's decision, if any -- sourced from the
  // SAME run latest_done_check_run_id points at.
  latest_decision: Decision | null;
  // V-041 / ux-critic finding: which rubric FAMILY the latest DONE run
  // used -- lets a bulk re-run UI exclude a manuscript checked only
  // under a completely unrelated format, instead of defaulting it to
  // selected and burning quota grading it against the wrong rubric.
  latest_done_rubric_family_id: string | null;
}

export interface PaginatedManuscripts {
  items: ManuscriptListItem[];
  total: number;
  page: number;
  page_size: number;
}

/** Response of `POST /manuscripts/ingest` (V-059) -- what the upload
 * screen shows immediately after a successful upload. `section_tree` is
 * deliberately omitted -- nothing in this UI renders it. */
export interface IngestSummary {
  manuscript_id: number;
  ingest_status: string;
  page_count: number;
  anchor_kind: "page" | "paragraph";
  image_only: boolean;
  text_chars: number;
  images: number;
  tables: number;
  equations: number;
  citations: number;
  vision_status: "none" | "done" | "unavailable";
  notes: string[];
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
  n_criteria?: number;
  /** Only present when this stage actually degraded (screen 4g, V-055) —
   * never set on a clean run, so its presence alone is the check. */
  degraded_count?: number;
  degraded_code?: string;
  note?: string;
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
  manuscript_group_label: string | null;
  manuscript_original_filename: string | null;
  manuscript_uploaded_at: string | null;
  rubric_title: string | null;
}

export interface EvidenceItem {
  quote: string;
  anchor: string;
}

/** Present only when an instructor resolved this criterion out of the
 * escalation panel — the report must show this distinctly, never as an
 * ordinary AI-graded row (V-055 review). */
export interface ResolutionOut {
  type: "accept_majority" | "mark_pass" | "mark_fail";
  reason: string;
  ai_majority_verdict: string | null;
}

// BUG-044 (High, live-reproduced): `ResultsTable`/`DecisionSummary`/
// `explainer` are shared between the instructor's own authenticated
// report (Report.tsx) and the public, unauthenticated adviser view
// (AdviserView.tsx) -- before this fix they typed their props as the
// full `ReportOut`/`CriterionResultOut` directly, so the public payload
// (`SharedReportOut`) being typed as literally `ReportOut` meant any
// field ever added to it (V-041's `previous_status`, the per-criterion
// `resolution` -- the instructor's own private override reasoning) was
// silently readable by anyone with the link, no cookie required. These
// two "*Common" interfaces are the actual security boundary now: they
// name exactly the fields safe for a public reader, and both `ReportOut`
// and the new `PublicReportOut` below satisfy them structurally --
// adding a field to `ReportOut` alone can never again make it reach a
// shared component's public rendering path.
export interface ResultRowCommon {
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
  // Optional, not absent: `CriterionResultOut` (instructor-facing) always
  // carries a real value; `PublicCriterionResultOut` (BUG-044) doesn't
  // declare the field at all, which TypeScript treats as compatible with
  // an optional property here -- `if (row.resolution)` reads correctly
  // either way.
  resolution?: ResolutionOut | null;
}

export interface CriterionResultOut extends ResultRowCommon {
  resolution: ResolutionOut | null;
}

/** BUG-044 fix: the public, unauthenticated adviser view's per-criterion
 * row. Deliberately does NOT extend `CriterionResultOut` or declare a
 * `resolution` field -- see `ResultRowCommon`'s own comment. */
export type PublicCriterionResultOut = ResultRowCommon;

export interface ReportCommon {
  status: "ready" | "conditionally_ready" | "not_ready" | "needs_review";
  composite_score: number | null;
  thresholds: { ready_min_score: number; not_ready_max_score: number };
  reason: string | null;
  flag_deduction: number;
  unresolved_high_flag_count: number;
  // V-038 (F8.5) — the terminal gate. `decision` is null until the
  // instructor decides; once set, the report is frozen until an explicit
  // reopen.
  decision: Decision | null;
  decided_at: string | null;
  // V-040's ShareModal.tsx/DecisionModal.tsx copy already discloses this
  // specifically before an instructor shares or writes one -- the one
  // decision-adjacent field the public payload legitimately carries too.
  decision_note: string | null;
  // False when the rubric used for this run is no longer the active
  // version for its family.
  rubric_is_current: boolean;
  // BUG-049: "fake" (fixture data, no real Gemini call), "real", or
  // "unknown" (a run that predates this field) -- shown wherever this
  // report's verdict is shown so fixture-derived flags/scores can never
  // be mistaken for real findings about the manuscript.
  llm_mode: "fake" | "real" | "unknown";
}

export interface ReportOut extends ReportCommon {
  check_run_id: number;
  manuscript_group_label: string;
  manuscript_original_filename: string | null;
  rubric_title: string;
  results: CriterionResultOut[];
  // How many criteria still need review -- gates the decide action; the
  // server re-checks authoritatively regardless. Instructor-only: BUG-044
  // found this published to anonymous readers for no reason (the public
  // view derives an equivalent count from `results` itself).
  pending_review_count: number;
  // V-041 — the version-comparison line: the same manuscript's most
  // recent OTHER done+reported run, if one exists. Instructor-only:
  // BUG-044 found this published a DIFFERENT, never-shared check run's
  // own score to anyone holding a link for a different one.
  previous_status: "ready" | "conditionally_ready" | "not_ready" | "needs_review" | null;
  previous_composite_score: number | null;
}

/** BUG-044 fix: the public, unauthenticated adviser view's report
 * payload. Deliberately does NOT extend `ReportOut` -- see
 * `ReportCommon`'s own comment for which fields were cut and why. */
export interface PublicReportOut extends ReportCommon {
  check_run_id: number;
  manuscript_group_label: string;
  manuscript_original_filename: string | null;
  rubric_title: string;
  results: PublicCriterionResultOut[];
}

// V-041: shown before confirming a bulk re-run. Each manuscript's own
// most recent DONE run's real measured call count is the estimate --
// null when it has never been checked before (nothing to estimate from).
export interface RerunEstimateItem {
  manuscript_id: number;
  estimated_calls: number | null;
}

export interface RerunEstimateOut {
  items: RerunEstimateItem[];
  total_estimated_calls: number;
  manuscripts_with_no_estimate: number;
}

export type Decision = "approved" | "returned" | "rejected";

export interface DecisionIn {
  decision: Decision;
  note?: string | null;
}

export interface ReopenIn {
  reason: string;
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
  // Only on the detail view, not AuditLogSummary's list rows -- the list
  // stays deliberately light at volume (BUG-022).
  manuscript_original_filename: string | null;
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
  /** "low_confidence" = AI graded it and hesitated; "not_graded" = the AI
   * never ran (daily capacity spent or API down). Different evidence, so
   * the panel must label them differently (V-050). */
  review_reason: "low_confidence" | "not_graded";
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
  check_run_id: number;
  manuscript_group_label: string;
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
  // BUG-049: the flag evidence page is exactly where the audit found a
  // fabricated statistical-forensics finding rendered with no disclosure.
  llm_mode: "fake" | "real" | "unknown";
}

export interface OverrideFlagOut extends FlagOut {
  report: ReportOut;
}

/** BUG-033: the report's own flags list — a deliberately smaller field
 * set than FlagOut (see backend/app/report/schemas.py's own docstring
 * for why: a summary row's job is "enough to decide whether to click,"
 * not "enough to skip clicking"). */
export interface FlagSummaryOut {
  id: number;
  check_kind: string;
  severity: "high" | "med" | "low";
  criterion_text: string | null;
  evidence_excerpt: string;
  page_anchor: string;
  overridden: boolean;
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
  /** V-052 (BYOK): "own" only when this instructor has their own Gemini
   * key configured and this status reflects THEIR island; "shared"
   * otherwise (including fake mode, which spends no real key of either
   * kind). */
  key_source: "own" | "shared";
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

// V-042 (screen 4t) — the F7 originality/reuse archive: which manuscripts
// still have an embedding archive, and per-item purge.
export interface ArchiveItemOut {
  manuscript_id: number;
  group_label: string;
  original_filename: string | null;
  created_at: string;
  latest_check_run_status: CheckRunStatus | null;
  has_archive: boolean;
  purged_at: string | null;
}

export interface PaginatedArchive {
  items: ArchiveItemOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface PurgeOut {
  manuscript_id: number;
  purged_at: string;
}

// V-042 (screen 4u) — read-only transparency: scoring/escalation
// thresholds (F8.9 isn't built yet, so `editable` is always false today)
// and which prompt/model versions actually ran, sourced live from the
// audit log (never a static/hardcoded version table).
export interface ThresholdsOut {
  ready_min_score: number;
  not_ready_max_score: number;
  escalation_agreement_threshold: number;
  editable: boolean;
}

export interface PromptVersionOut {
  prompt_type: string;
  prompt_version: string;
  model: string;
  observed_at: string;
}

/** V-052 (BYOK). Never the key itself, encrypted or not. */
export interface ApiKeyStatusOut {
  byok_available: boolean;
  has_own_api_key: boolean;
}

export interface SettingsOut {
  thresholds: ThresholdsOut;
  prompt_versions: PromptVersionOut[];
  api_key: ApiKeyStatusOut;
}

export interface SetApiKeyIn {
  api_key: string;
}

export interface ChangePasswordIn {
  current_password: string;
  new_password: string;
}

// V-040 (screens 4k-4l) — read-only, tokenized report sharing (F8.7).
// No adviser accounts; the token itself is the credential.
export interface CreateShareLinkIn {
  expires_at: string | null;
}

export interface ShareLinkOut {
  token: string;
  check_run_id: number;
  created_at: string;
  expires_at: string | null;
}

// The public, unauthenticated adviser view's payload. BUG-044: used to
// be typed as ReportOut itself ("safe by never being reachable through
// this route" turned out false the moment a shared component read a
// field that route's data actually carried) -- now PublicReportOut, an
// independently-typed, deliberately smaller shape.
export interface SharedReportOut {
  report: PublicReportOut;
  flags: FlagSummaryOut[];
}
