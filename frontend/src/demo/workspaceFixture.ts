import type { CheckRun, FlagOut, LibraryItemOut, ManuscriptListItem, ReportOut, Rubric } from '../api/types';
import official from './officialRubric.json';
import review from './reviewData.json';
import assets from './assets.json';
import cases from './moduleCases.json';
import assessed from './assessedReview.json';

export const DEMO_ENABLED = import.meta.env.VITE_PUBLIC_DEMO === 'true';
export const isWorkspaceSample = () => DEMO_ENABLED && typeof window !== 'undefined' && /^\/demo(?:\/|$)/.test(window.location.pathname);
export const WORKSPACE_SAMPLE = {
  prefix: '/demo', storageKey: 'veridical-assessed-rubric-v3',
  title: 'Stock & Inventory Management System', filename: 'Capstone_G2_Final.pdf',
  pdfAsset: '/sample-assets/stock-inventory-capstone.pdf',
  sha256: assets[0].sha256, pageCount: assets[0].pages, preparedAt: '2026-10-30T00:00:00Z',
};
export const sampleInstructor = { id: 90001, email: 'instructor@example.invalid', display_name: 'Capstone Instructor', onboarding_dismissed_at: WORKSPACE_SAMPLE.preparedAt };
export const sampleRubric: Rubric = { ...official as Rubric, can_edit_criteria: false };
export const unassessedReport: ReportOut = {
  assessment_method: 'manual_source_review',
  check_run_id: 1, assessment_revision: 1, manuscript_id: 1,
  manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename,
  rubric_title: official.title, status: 'needs_review', composite_score: null,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: 'Review the manuscript against the nine official criteria and record your judgment. Automated assessment is unavailable for this record.',
  flag_deduction: 0, unresolved_high_flag_count: 0, decision: null, decided_at: null, decision_note: null,
  rubric_is_current: true, llm_mode: 'unknown', rubric_needs_review: false, rubric_parse_issues: null,
  pending_review_count: 9, previous_status: null, previous_composite_score: null,
  results: review.results as ReportOut['results'], integrity_check_status: [],
  integrity_modules: [
    { check_kind: 'internal_agreement', state: 'partial' },
    { check_kind: 'citation_integrity', state: 'partial' },
    { check_kind: 'statistical_forensics', state: 'not_applicable' },
    { check_kind: 'originality_reuse', state: 'not_recorded' },
  ],
  statistical_check_scope: { state: 'not_applicable' }, originality_scope: null,
};
export const sampleReport: ReportOut = {
  ...unassessedReport, assessment_revision: assessed.judgments.length + 1,
  pending_review_count: 0, status: 'not_ready',
  reason: 'All nine criteria have recorded source-review judgments. The manuscript requires revision, particularly its evidence, references and bibliography.',
  results: unassessedReport.results.map(row => {
    const judgment = assessed.judgments.find(item => item.criterion_id === row.criterion_id)!;
    const criterion = sampleRubric.criteria.find(item => item.id === row.criterion_id)!;
    const selected = criterion.levels!.find(item => item.level === judgment.level)!;
    const maximum = Math.max(...criterion.levels!.map(item => item.points));
    return { ...row, judgment_revision: 1, outcome: 'passed', basis: 'instructor', score: selected.points / maximum * 100,
      level: { name: selected.name, ordinal: selected.level, points: selected.points, max_points: maximum },
      resolution: { type: 'mark_level', reason: judgment.reason, ai_majority_verdict: null } };
  }),
};
sampleReport.composite_score = sampleReport.results.reduce((sum, row) => sum + row.score! * row.weight, 0)
  / sampleReport.results.reduce((sum, row) => sum + row.weight, 0);
export const sampleFlags: FlagOut[] = review.flags.map(flag => {
  const statements = review.references.filter(ref => ref.flag_id === flag.id);
  const intent = statements.find(ref => ref.role === 'agreement_intent');
  const outcome = statements.find(ref => ref.role === 'agreement_outcome');
  return { ...flag, agreement_pair: intent && outcome ? { intent_text: intent.quote, intent_anchor: intent.anchor,
    outcome_text: outcome.quote, outcome_anchor: outcome.anchor } : null } as FlagOut;
});
export const sampleRun: CheckRun = {
  id: 1, manuscript_id: 1, rubric_id: 1, status: 'done',
  stage_status: { stages: { semantic: { status: 'skipped', note: 'Awaiting instructor judgment; automated assessment unavailable.' }, integrity: { status: 'done', note: 'Source observations recorded. Statistical results and related passages are available in their respective check records.' } } },
  cancel_requested_at: null, queue_position: null, started_at: WORKSPACE_SAMPLE.preparedAt,
  finished_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt,
  manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename,
  manuscript_uploaded_at: WORKSPACE_SAMPLE.preparedAt, rubric_title: official.title,
};
export const sampleManuscript: ManuscriptListItem = {
  id: 1, group_label: WORKSPACE_SAMPLE.title, original_filename: WORKSPACE_SAMPLE.filename,
  ingest_status: 'done', ingest_failure_reason: null, ingest_failure_detail: null,
  created_at: WORKSPACE_SAMPLE.preparedAt, latest_check_run_id: 1, latest_check_run_status: 'done',
  latest_done_check_run_id: 1, latest_decision: null, latest_readiness: sampleReport.status,
  latest_done_rubric_family_id: official.rubric_family_id, escalations_awaiting_review: sampleReport.pending_review_count,
  program: 'Software Engineering',
};
export const librarySources = [
  { id: 202, title: 'e-AIMSS: Electronic Asset Inventory and Management System in School', authors: ['Antonio C. Ahmad'], year: '2023', asset: assets[1] },
  ...cases.documents.map((d, i) => ({ id: 203 + i, title: d.title, authors: d.authors, year: String(d.year), asset: assets.find(a => a.path === d.pdf_asset)! })),
];
export const sampleLibrary: LibraryItemOut[] = librarySources.map(d => ({
  manuscript_id: d.id, is_own: true, label: d.title, title: d.title, team_name: null,
  authors: d.authors, program: 'Published research', school_year: d.year,
  original_filename: d.asset.path.split('/').at(-1)!, status: 'active', source: 'library_submission',
  added_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt,
  proposed_fields: [], possible_duplicate_of: null, failure_detail: null,
}));
