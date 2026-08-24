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

// BUG-092: "not_assessable" -- a real defense-day/physical requirement
// (e.g. "brings three bound copies") no manuscript could ever settle.
// Routed to a terminal `not_applicable` result, never AI-graded, never
// escalated.
export type CriterionType = "structural" | "semantic" | "not_assessable";

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
  // V-064 (AC1): a family-level attribute. null = "Not set" -- never
  // guessed, same convention as ManuscriptListItem.program.
  program: string | null;
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
  // V-071 (AC1): how many of the latest done run's criteria are escalated
  // and awaiting review -- lets the row itself say "this one needs you"
  // instead of only a dashboard-wide total with nothing to point at.
  escalations_awaiting_review: number;
  // V-062 (AC5): the manuscript's group's program, sourced through the
  // Group entity. null = "Not set" -- never guessed, same convention as
  // ingest_failure_reason.
  program: string | null;
}

// V-062: a real Group entity, replacing free-text group_label as the
// identity mechanism -- read-only from the frontend today (no dedicated
// create/manage screen; a Group is created implicitly by uploading with
// its name).
export interface GroupOut {
  id: number;
  name: string;
  program: string | null;
}

export interface ProgramOut {
  id: number;
  name: string;
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
// V-063: a proposed value plus WHERE it came from (page/paragraph
// anchor) -- the same evidence standard the rest of the product is held
// to (screens 4h/4i already use this "value + anchor" shape).
export interface AnchoredValue {
  value: string;
  anchor: string;
}

export interface TitlePageProposal {
  title: AnchoredValue | null;
  short_name: AnchoredValue | null;
  members: AnchoredValue[];
  program: AnchoredValue | null;
  adviser: AnchoredValue | null;
  // true = nothing usable found at all (e.g. an image-only scan) -- a
  // PARTIAL proposal (title found, no members) is NOT a failure and is
  // shown as-is, gaps included, never fabricated.
  extraction_failed: boolean;
}

export interface ConfirmGroupResponse {
  group_id: number;
  group_label: string;
  program: string | null;
  // V-066: null unless this confirm was the group's FIRST (a later
  // manuscript matched into an existing group never overwrites it).
  title: string | null;
  // true = an existing group was matched (a real resubmission); false =
  // a brand-new group was created.
  matched: boolean;
}

// V-063 (owner's call, 2026-08-19): rule 3 of the matching rule (ticket's
// DECIDED section, "PROPOSE the match, do not apply it") surfaced to the
// confirm form BEFORE the instructor confirms, not only disclosed after
// the fact via a disambiguating suffix.
export interface GroupCollisionOut {
  collision: boolean;
  existing_group_name: string | null;
}

export interface IngestSummary {
  manuscript_id: number;
  // BUG-043: echoes back what the server actually persisted, so a client
  // that sent a group label has a way to notice if it was ever dropped.
  group_label: string;
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
  // V-063: the auto-proposed group/program, deterministically extracted
  // from the title page -- the confirm dialog's own data source.
  group_proposal: TitlePageProposal;
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
  type: EscalationResolution;
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
  // D-023: the raw relative weight (no required total) -- kept for
  // completeness but NOT what the UI displays; see `weight_importance`.
  weight: number;
  // D-023 (BUG-051/052/098): this criterion's weight bucketed into the
  // same Low/Medium/High scale flag severity uses (`SeverityTag.tsx`),
  // computed server-side from its share of this run's total weight --
  // never rendered as a raw percentage, which asserted a scale a
  // relative value doesn't have.
  weight_importance: "low" | "med" | "high";
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
  // BUG-052: whether the rubric this run graded against was confirmed
  // while the parser's own coverage gate still flagged it as needing
  // manual completion -- survives activation now (it used to be silently
  // wiped the instant Confirm was clicked) so the report can keep
  // disclosing that the measuring instrument was flagged.
  rubric_needs_review: boolean;
  rubric_parse_issues: string[] | null;
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
  /** V-068: quotes the model actually returned but that failed containment
   * verification. Never a verified anchor -- rendered under its own
   * "could not verify" label, never inside a verified evidence block. */
  unverified_evidence: string[] | null;
}

export type EscalationResolution = "accept_majority" | "mark_pass" | "mark_fail" | "needs_document";

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

// V-072 (F7.4): present only on a passage-level reuse flag. `matched_ref`
// is the same opaque, non-identifying manuscript id every other F7 flag
// already uses (BUG-050/097) -- never a real name or heading.
// `matched_excerpt`/`matched_context_before`/`matched_context_after` are
// bounded, stored text (never a live read of the matched manuscript's
// file -- bounded-excerpt rule, carried from V-058/BUG-050 Branch B).
export interface PassagePairOut {
  own_excerpt: string;
  own_context_before: string | null;
  own_context_after: string | null;
  matched_ref: number;
  matched_excerpt: string;
  matched_context_before: string | null;
  matched_context_after: string | null;
  context_words_each_side: number;
  similarity: number;
  level: "exact_duplicate" | "high_similarity";
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
  passage_pair: PassagePairOut | null;
  // BUG-097 (presentation-only remedy, owner ruling 2026-08-24): true only
  // for an F7 flag produced on the account's first-ever manuscript upload.
  // Never changes `severity`.
  first_upload_context: boolean;
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
  // V-072 (F7.4): distinguishes a passage-level reuse flag from today's
  // whole-document/chapter-level ones (same check_kind).
  is_passage_level: boolean;
  // BUG-097: mirrors FlagOut's own field.
  first_upload_context: boolean;
}

// V-065: what the manuscript viewer's document pane can actually do with
// one flag's anchor -- never more than `app.ingest.regions.recover_region`
// measured it can recover (real 47-page manuscript, 2026-08-19). "kind"
// drives which affordance renders; every other field is null unless that
// kind uses it.
export type AnchorRegionKind =
  | "page_bbox"
  | "page_only"
  | "reference_list"
  | "reference_position"
  | "section"
  | "whole_document"
  | "paragraph_only"
  | "unavailable";

export interface FlagRegionOut {
  flag_id: number;
  kind: AnchorRegionKind;
  page: number | null;
  end_page: number | null;
  bbox: [number, number, number, number] | null;
  all_bboxes: [number, number, number, number][];
  paragraph: number | null;
  index: number | null;
}

export interface ManuscriptViewerOut {
  manuscript_id: number;
  original_filename: string | null;
  source_format: "pdf" | "docx" | "unknown";
  available: boolean;
  unavailable_reason: string | null;
  purged_at: string | null;
  page_count: number | null;
  regions: FlagRegionOut[];
}

// V-065 AC1 (DOCX gap): the reconstructed-text pane's actual content.
// `paragraph` matches a FlagRegionOut's own `paragraph` field verbatim --
// both are `TextBlock.paragraph` (app/ingest/schemas.py), the 0-based
// body-item ordinal `¶N` anchors already carry.
export interface DocumentParagraphOut {
  paragraph: number;
  text: string;
  heading_level: number | null;
}

export interface DocumentParagraphsOut {
  paragraphs: DocumentParagraphOut[];
}

// V-072 (F7.4), `ui-designer` spec §4.2/§4.3: a passage match the DEFAULT
// policy excludes from scoring (own or matched side falls inside the
// reference list or a detected block quote) -- revealed only when the
// instructor turns on the matching exploration toggle. Never a real
// `Flag` -- `id` is a request-scoped string, `own_region.flag_id` is a
// synthetic negative int, neither can be confused with a real Flag.id.
export interface ExcludedReuseMatchOut {
  id: string;
  own_excerpt: string;
  own_context_before: string | null;
  own_context_after: string | null;
  own_region: FlagRegionOut;
  matched_ref: number;
  matched_excerpt: string;
  matched_context_before: string | null;
  matched_context_after: string | null;
  context_words_each_side: number;
  similarity: number;
  level: "exact_duplicate" | "high_similarity";
  excluded_reason: ("reference_list" | "block_quote")[];
}

export interface ReuseMatchesOut {
  passage_archive_size_n: number;
  matches: ExcludedReuseMatchOut[];
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

// V-042: per-item purge of the F7 embedding archive (originally screen
// 4t, now folded into the Library screen, V-066 -- Library is a strict
// superset of what the old Archive screen showed).
export interface PurgeOut {
  manuscript_id: number;
  purged_at: string;
}

// V-066 (screen 4w) — the shared-corpus library: replaces the Archive
// screen above (a strict superset -- every own manuscript still gets its
// archive state + purge action, plus every other account's, plus real
// metadata Archive never showed).
export interface LibraryItemOut {
  manuscript_id: number;
  group_label: string;
  title: string | null;
  authors: string[];
  program: string | null;
  original_filename: string | null;
  created_at: string;
  purged_at: string | null;
  is_own: boolean;
}

export interface PaginatedLibrary {
  items: LibraryItemOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface LibraryChapterExcerptOut {
  chapter_index: number;
  title: string;
  excerpt: string | null;
  context_before: string | null;
  context_after: string | null;
}

// The bounded, cross-tenant-safe detail view (Q2's ruling) -- never the
// full document, always available regardless of ownership.
export interface LibraryExcerptOut {
  manuscript_id: number;
  chapters: LibraryChapterExcerptOut[];
  total_chapters: number;
  limitations: string;
  purged_at: string | null;
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
