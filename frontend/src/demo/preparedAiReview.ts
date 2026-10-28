import type { AuditLogDetail, CheckRun, ManuscriptListItem, ReportOut, ResultRowCommon, Rubric } from "../api/types";
import { sampleManuscript, sampleReport, sampleRubric, sampleRun, WORKSPACE_SAMPLE } from "./workspaceFixture";

export const AI_REVIEW_BASIS = "prepared_ai_review";
export const AI_REVIEW_CONFIRMED_BASIS = "instructor_after_prepared_ai_review";
export const AI_REVIEW_VERSION = "source-review-20261028-2";
export const AI_REVIEWED_AT = "2026-10-28T09:31:47Z";
export const AI_REVIEW_DESCRIPTION = "The manuscript was reviewed by an AI assistant in this session. The app's automated grading service was not run. The teacher makes the final defense decision.";

// This assessment uses the categories, weights and named levels printed on the
// published paper's page 1. It is an AI review, not an instructor resolution.
export const AI_REVIEW_GRADES: Readonly<Record<number, { level: number; reason: string }>> = {
  1: { level: 2, reason: "Developing. The abstract on page 5 explains the C console program clearly, and the code and manual show what was built. However, page 20 describes a web system and database, page 29 says there is no database, and page 109 claims database integration. Pages 103-108 list that work as future development. These conflicting descriptions make the completed scope hard to follow. Separate completed work from plans and revise the conclusion." },
  2: { level: 2, reason: "Developing. The paper includes requirements, diagrams, substantial C code and a user manual, so it documents a concrete prototype. The evaluation on page 23 describes planned tests but gives no measured results or user sample. The component summary on pages 32-33 does not match its listed rows. The research claim on page 7 has no identified study, and page 109 supplies no bibliography. Add test results and research references, and reconcile the component totals." },
  3: { level: 3, reason: "Sufficient. The main technical descriptions are understandable and the abstract presents the purpose and features in connected prose. Proofreading is still needed: page 3 has subject-verb errors and 'top any other', page 4 changes between 'We', 'I' and 'our', and page 22 has 'develpoers'. These examples lower the polish, but do not prevent understanding of the documented prototype. Correct them and check consistency throughout the paper." },
  4: { level: 3, reason: "Sufficient. The contents page locates the requirements, design diagrams, methods, evaluation, limitations, source code and user manual. The paper includes those materials and separates future work from the implementation sections. Formatting is usable, but page 6 leaves an objective entry blank, the reference section needs completion, and headings and spacing need a consistency pass. Page 4 already contains an inventory-system logo. Replace the reference-page acknowledgement with a proper bibliography." },
};

export const hasPreparedAiGrades = (report: { results: readonly Pick<ResultRowCommon, "basis">[] }) => report.results.some(row => row.basis === AI_REVIEW_BASIS || row.basis === AI_REVIEW_CONFIRMED_BASIS);

export function applyPreparedAiReview(state: { report: ReportOut; rubric: Rubric; run: CheckRun; manuscript: ManuscriptListItem; events: AuditLogDetail[]; fileAvailable: boolean; aiReviewVersion?: string }): boolean {
  if (!state.run || !state.manuscript || !state.rubric || !Array.isArray(state.report?.results)) return false;
  if (state.aiReviewVersion || !state.fileAvailable || state.report.decision || state.report.check_run_id !== sampleReport.check_run_id || state.report.manuscript_id !== sampleReport.manuscript_id) return false;
  if (state.report.llm_mode !== "manual" || JSON.stringify(state.rubric.criteria) !== JSON.stringify(sampleRubric.criteria)) return false;
  if (state.run.id !== sampleRun.id || state.run.manuscript_id !== sampleRun.manuscript_id || state.run.rubric_id !== sampleRun.rubric_id || state.run.status !== "done" || state.manuscript.id !== sampleManuscript.id) return false;
  if (state.rubric.id !== sampleRubric.id || state.rubric.rubric_family_id !== sampleRubric.rubric_family_id || state.rubric.version !== sampleRubric.version || state.rubric.title !== sampleRubric.title) return false;
  if (state.events.some(event => ["escalation_resolved", "decision_recorded", "decision_reopened", "rubric_confirmed", "rubric_uploaded"].includes(event.event_type))) return false;
  if (state.report.results.length !== sampleReport.results.length || state.report.results.some((row, index) => {
    const original = sampleReport.results[index];
    return row.criterion_id !== original.criterion_id || row.text !== original.text || row.weight !== original.weight || row.type !== original.type || row.kind !== original.kind || row.anchor !== original.anchor || row.basis !== original.basis || row.outcome !== "escalated" || row.score !== null || row.level || row.resolution || row.reasoning !== original.reasoning || JSON.stringify(row.evidence) !== JSON.stringify(original.evidence);
  })) return false;
  for (const row of state.report.results) {
    const grade = AI_REVIEW_GRADES[row.criterion_id];
    const criterion = sampleRubric.criteria.find(item => item.id === row.criterion_id)!;
    const level = criterion.levels!.find(item => item.level === grade.level)!;
    row.level = { name: level.name, ordinal: level.level, points: level.points, max_points: criterion.weight };
    row.score = level.points / criterion.weight * 100;
    row.outcome = "passed";
    row.basis = AI_REVIEW_BASIS; row.reasoning = grade.reason; row.reason = null;
  }
  state.aiReviewVersion = AI_REVIEW_VERSION;
  state.report.reason = "The completed AI review gives Developing for Clarity and Content Quality, and Sufficient for Spelling & Grammar and Organization and Formatting. The resulting readiness band is Conditionally Ready. Clarify the delivered scope, add evaluation results and research references, reconcile component totals, and proofread before the teacher's final decision.";
  return true;
}

export const preparedAiReviewReceipt = { review_version: AI_REVIEW_VERSION, reviewed_at: AI_REVIEWED_AT, source_sha256: WORKSPACE_SAMPLE.sha256, review_basis: AI_REVIEW_BASIS };

export const LEGACY_ORGANIZATION_REVIEW = "Sufficient. The contents page locates the requirements, design diagrams, methods, evaluation, limitations, source code and user manual. The paper includes those materials and separates future work from the implementation sections. Formatting is usable, but page 6 leaves an objective entry blank, page 4 retains a LOGO placeholder, and headings and spacing need a consistency pass. Replace the reference-page acknowledgement with a proper bibliography.";
export function organizationSourceCorrection(criterionId: number, reasoning: string | null): string | null {
  return criterionId === 4 && reasoning?.includes(LEGACY_ORGANIZATION_REVIEW) ? "Source correction: page 4 of the published sample contains a logo. The earlier placeholder remark is unsupported." : null;
}
