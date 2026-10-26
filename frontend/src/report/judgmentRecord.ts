import type { JudgmentContext, JudgmentHistoryEntry, JudgmentRecordInput, JudgmentRevisionInput, JudgmentRevisionOutput, JudgmentSnapshot } from "../api/judgments";
import type { CriterionResultOut, ReportOut, ResultRowCommon } from "../api/types";
import { CRITERION_SCORE_DECIMAL_PLACES, CRITERION_SCORE_UNITS } from "../config/ui";
import { CRITERION_EXCLUSION_COPY } from "../domain/criterionReview";
import { resolutionRequiresReason } from "../domain/criterionReview";
import { isAssessmentRevision } from "./assessmentRevision";

export function judgmentVersion(row: ResultRowCommon): number | undefined {
  const value = (row as CriterionResultOut).judgment_revision;
  return isAssessmentRevision(value) ? value : undefined;
}

export type JudgmentProposal = Pick<JudgmentSnapshot, "resolution_type" | "outcome" | "score" | "level" | "reason">;

export function judgmentLabel(value: Pick<JudgmentSnapshot, "resolution_type" | "outcome" | "score" | "level">): string {
  if (value.resolution_type === "needs_document") return CRITERION_EXCLUSION_COPY.recorded;
  if (value.level?.name.trim()) return value.level.name;
  if (value.resolution_type === "mark_level") return "Recorded grade unavailable";
  if (value.outcome === "failed") return "Does not meet";
  return value.score === CRITERION_SCORE_UNITS.full ? "Meets criterion" : "Partly meets criterion";
}

export function snapshotValid(value: JudgmentSnapshot | null): value is JudgmentSnapshot {
  if (!value || !isAssessmentRevision(value.judgment_revision) || typeof value.reason !== "string"
    || (resolutionRequiresReason(value.resolution_type) && !value.reason.trim())
    || !["recorded", "legacy_partial"].includes(value.provenance)) return false;
  const { none, full } = CRITERION_SCORE_UNITS;
  if (value.score !== null && (typeof value.score !== "number" || !Number.isFinite(value.score)
    || value.score < none || value.score > full)) return false;
  if (value.level && (typeof value.level.name !== "string" || !value.level.name.trim()
    || !Number.isSafeInteger(value.level.ordinal) || !Number.isFinite(value.level.points)
    || !Number.isFinite(value.level.max_points) || value.level.max_points <= 0
    || value.level.points < 0 || value.level.points > value.level.max_points)) return false;
  if (value.actor_id !== null && (!Number.isSafeInteger(value.actor_id) || value.actor_id <= 0)) return false;
  if (value.recorded_at !== null && (typeof value.recorded_at !== "string" || !/(Z|[+-]\d\d:\d\d)$/i.test(value.recorded_at)
    || !Number.isFinite(Date.parse(value.recorded_at)))) return false;
  if (value.provenance === "recorded" && (!value.judgment_revision || value.actor_id === null || value.recorded_at === null
    || (value.resolution_type === "mark_level" && !value.level))) return false;
  if (value.resolution_type === "needs_document") return value.outcome === "not_applicable" && value.score === null;
  if (value.resolution_type === "mark_pass") return value.outcome === "passed" && value.score === full;
  if (value.resolution_type === "mark_fail") return value.outcome === "failed" && value.score === none;
  return (value.resolution_type === "mark_level" || value.resolution_type === "accept_majority")
    && value.score !== null && (value.outcome === "passed" || (value.resolution_type === "accept_majority" && value.outcome === "failed" && value.score === none));
}

export function contextMatches(value: JudgmentContext, run: number, criterion: number): boolean {
  return Boolean(value && value.check_run_id === run && value.criterion_id === criterion
    && Number.isSafeInteger(value.check_result_id) && value.check_result_id > 0
    && isAssessmentRevision(value.assessment_revision) && typeof value.criterion_text === "string"
    && (value.current === null || snapshotValid(value.current))
    && value.choices && ["pass_fail", "levelled", "unavailable"].includes(value.choices.kind)
    && Array.isArray(value.choices.levels) && value.choices.levels.every(level => Number.isSafeInteger(level.level)
      && typeof level.name === "string" && level.name.trim() && typeof level.descriptor === "string"
      && Number.isFinite(level.points) && level.points >= 0)
    && typeof value.can_revise === "boolean" && [null, "approved", "returned", "rejected"].includes(value.decision)
    && (value.can_record === undefined || typeof value.can_record === "boolean")
    && (!value.can_record || (!value.can_revise && value.current === null && value.decision === null
      && value.choices.kind !== "unavailable" && originalAssessmentValid(value.original_assessment)))
    && (!value.can_revise || (value.current !== null && value.decision === null && value.choices.kind !== "unavailable"))
    && (value.choices.kind !== "levelled" || (value.choices.levels.length > 1
      && new Set(value.choices.levels.map(level => level.level)).size === value.choices.levels.length
      && value.choices.levels.some(level => level.points > 0))));
}

export function sameJudgmentGrade(a: Omit<JudgmentProposal, "reason">, b: Omit<JudgmentProposal, "reason">): boolean {
  return a.outcome === b.outcome && a.score === b.score
    && a.level?.ordinal === b.level?.ordinal && a.level?.name === b.level?.name
    && a.level?.points === b.level?.points && a.level?.max_points === b.level?.max_points
    && (a.resolution_type === "needs_document") === (b.resolution_type === "needs_document")
    && (a.resolution_type === "mark_level" && !a.level) === (b.resolution_type === "mark_level" && !b.level);
}

export function originalAssessmentValid(value: JudgmentContext["original_assessment"] | null | undefined): boolean {
  if (!value || !["structural", "semantic"].includes(value.kind)
    || !["passed", "failed"].includes(value.original_outcome ?? "")
    || typeof value.original_score !== "number" || !Number.isFinite(value.original_score)
    || value.original_score < CRITERION_SCORE_UNITS.none || value.original_score > CRITERION_SCORE_UNITS.full
    || (value.original_outcome === "failed" && value.original_score !== CRITERION_SCORE_UNITS.none)) return false;
  const level = value.original_level;
  if (level === null) return true;
  if (!level || typeof level !== "object" || Array.isArray(level)) return false;
  return Number.isSafeInteger(level.ordinal) && typeof level.name === "string" && Boolean(level.name.trim())
    && typeof level.points === "number" && Number.isFinite(level.points) && typeof level.max_points === "number"
    && Number.isFinite(level.max_points) && level.max_points > 0 && level.points >= 0 && level.points <= level.max_points;
}

export function originalJudgmentLabel(value: JudgmentContext["original_assessment"]): string {
  if (!originalAssessmentValid(value)) return "Automated assessment unavailable";
  if (value.original_level?.name.trim()) return value.original_level.name;
  if (value.original_outcome === "failed") return "Does not meet";
  return value.original_score === CRITERION_SCORE_UNITS.full ? "Meets criterion" : "Partly meets criterion";
}

function originalAssessmentKey(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) : item);
}

export function recordedLevelScoreConsistent(value: JudgmentSnapshot): boolean {
  if (["mark_pass", "mark_fail", "needs_document"].includes(value.resolution_type) && value.level !== null) return false;
  if (value.resolution_type !== "mark_level") return true;
  if (!value.level || value.score === null) return false;
  const normalized = value.level.points / value.level.max_points * CRITERION_SCORE_UNITS.full;
  const storageTolerance = 10 ** -CRITERION_SCORE_DECIMAL_PLACES / 2
    + Number.EPSILON * CRITERION_SCORE_UNITS.full;
  return Math.abs(value.score - normalized) <= storageTolerance;
}

export function recordAcknowledged(out: JudgmentRevisionOutput, run: number, criterion: number,
  submitted: JudgmentRecordInput, bound: JudgmentContext, actor: number): boolean {
  if (!out || !contextMatches(out.context, run, criterion) || !snapshotValid(out.context.current)
    || bound.current !== null || !bound.can_record || !originalAssessmentValid(bound.original_assessment)
    || out.context.check_result_id !== bound.check_result_id || out.context.criterion_text !== bound.criterion_text
    || originalAssessmentKey(out.context.choices) !== originalAssessmentKey(bound.choices)
    || out.context.assessment_revision !== submitted.expected_revision + 1
    || out.context.can_record || !out.context.can_revise
    || out.context.current.judgment_revision !== 1 || out.context.current.provenance !== "recorded"
    || out.context.current.actor_id !== actor || out.context.current.resolution_type !== submitted.resolution
    || out.context.current.reason !== submitted.reason || !recordedLevelScoreConsistent(out.context.current)
    || (submitted.resolution === "mark_level" && out.context.current.level?.ordinal !== submitted.level)) return false;
  const entry = out.entry;
  if (submitted.resolution === "mark_level") {
    const expected = bound.choices.levels.find(level => level.level === submitted.level);
    if (!expected || out.context.current.level?.name !== expected.name
      || out.context.current.level.points !== expected.points
      || out.context.current.level.max_points !== Math.max(...bound.choices.levels.map(level => level.points))) return false;
  }
  const rows = out.report?.results?.filter(row => row.criterion_id === criterion);
  return Boolean(entry && entry.schema_version === 2 && entry.action === "judgment_recorded"
    && Number.isSafeInteger(entry.entry_id) && entry.entry_id > 0
    && entry.check_run_id === run && entry.criterion_id === criterion && entry.check_result_id === bound.check_result_id
    && entry.assessment_revision_before === submitted.expected_revision
    && entry.assessment_revision_after === out.context.assessment_revision && entry.before === null
    && snapshotValid(entry.after) && entry.after.judgment_revision === 1
    && sameJudgmentGrade(entry.after, out.context.current) && entry.after.resolution_type === submitted.resolution
    && entry.after.reason === submitted.reason
    && entry.after.provenance === "recorded" && entry.performed_by === actor && entry.after.actor_id === actor
    && entry.after.recorded_at === entry.performed_at && entry.after.recorded_at === out.context.current.recorded_at
    && originalAssessmentKey(entry.original_assessment) === originalAssessmentKey(bound.original_assessment)
    && originalAssessmentKey(out.context.original_assessment) === originalAssessmentKey(bound.original_assessment)
    && contextInReport(out.context, out.report) && out.report?.check_run_id === run && rows?.length === 1);
}

export function revisionAcknowledged(out: JudgmentRevisionOutput, run: number, criterion: number,
  submitted: JudgmentRevisionInput, before: JudgmentSnapshot, actor: number, expectedResult: number): boolean {
  if (!out || !contextMatches(out.context, run, criterion) || !snapshotValid(out.context.current)
    || out.context.check_result_id !== expectedResult
    || out.context.assessment_revision !== submitted.expected_revision + 1
    || out.context.current.judgment_revision !== submitted.expected_judgment_revision + 1
    || out.context.current.provenance !== "recorded" || out.context.current.actor_id !== actor
    || out.context.current.resolution_type !== submitted.resolution || out.context.current.reason !== submitted.reason
    || !recordedLevelScoreConsistent(out.context.current)
    || (submitted.resolution === "mark_level" && out.context.current.level?.ordinal !== submitted.level)) return false;
  const entry = out.entry;
  const row = out.report?.results?.filter(result => result.criterion_id === criterion);
  return Boolean(entry && entry.schema_version === 1 && Number.isSafeInteger(entry.entry_id) && entry.entry_id > 0
    && entry.action === "judgment_revised" && entry.check_run_id === run && entry.criterion_id === criterion
    && entry.check_result_id === out.context.check_result_id
    && entry.assessment_revision_before === submitted.expected_revision
    && entry.assessment_revision_after === out.context.assessment_revision
    && snapshotValid(entry.before) && snapshotValid(entry.after)
    && entry.before.judgment_revision === before.judgment_revision && sameJudgmentGrade(entry.before, before)
    && entry.before.reason === before.reason && entry.before.actor_id === before.actor_id
    && entry.before.recorded_at === before.recorded_at && entry.before.provenance === before.provenance
    && sameJudgmentGrade(entry.after, out.context.current) && entry.after.resolution_type === submitted.resolution
    && entry.after.reason === submitted.reason && entry.after.judgment_revision === out.context.current.judgment_revision
    && entry.performed_by === actor && entry.after.actor_id === actor && entry.after.recorded_at === entry.performed_at
    && entry.after.recorded_at === out.context.current.recorded_at && entry.after.provenance === "recorded"
    && out.report.check_run_id === run && out.report.assessment_revision === out.context.assessment_revision
    && row?.length === 1 && row[0].check_result_id === out.context.check_result_id
    && judgmentVersion(row[0]) === out.context.current.judgment_revision
    && row[0].outcome === out.context.current.outcome && row[0].score === out.context.current.score
    && row[0].resolution?.reason === submitted.reason && row[0].resolution?.type === submitted.resolution
    && row[0].level?.ordinal === out.context.current.level?.ordinal && row[0].level?.name === out.context.current.level?.name
    && row[0].level?.points === out.context.current.level?.points && row[0].level?.max_points === out.context.current.level?.max_points);
}

export function contextInReport(context: JudgmentContext, report: ReportOut | undefined): boolean {
  if (!report || report.check_run_id !== context.check_run_id || report.assessment_revision !== context.assessment_revision
    || report.decision !== context.decision || !Array.isArray(report.results)) return false;
  const rows = report.results.filter(row => row.criterion_id === context.criterion_id);
  const row = rows.length === 1 ? rows[0] : undefined;
  if (!row || row.check_result_id !== context.check_result_id) return false;
  const current = context.current;
  if (context.can_record) return current === null && row.resolution == null && row.judgment_revision == null
    && row.type !== "not_assessable" && ["structural", "semantic"].includes(row.kind)
    && row.outcome === context.original_assessment.original_outcome && row.score === context.original_assessment.original_score
    && row.level?.ordinal === context.original_assessment.original_level?.ordinal
    && row.level?.name === context.original_assessment.original_level?.name
    && row.level?.points === context.original_assessment.original_level?.points
    && row.level?.max_points === context.original_assessment.original_level?.max_points;
  return current === null || (row.judgment_revision === current.judgment_revision && row.outcome === current.outcome
    && row.score === current.score && row.resolution?.reason === current.reason && row.resolution.type === current.resolution_type
    && row.level?.ordinal === current.level?.ordinal && row.level?.name === current.level?.name
    && row.level?.points === current.level?.points && row.level?.max_points === current.level?.max_points);
}

export function historyEntryMatches(entry: JudgmentHistoryEntry, context: JudgmentContext): boolean {
  return Boolean(entry && Number.isSafeInteger(entry.entry_id) && entry.entry_id > 0
    && ((entry.schema_version === 1 && ["initial_resolution", "judgment_revised"].includes(entry.action)
      && entry.original_assessment == null) || (entry.schema_version === 2 && entry.action === "judgment_recorded"
      && originalAssessmentValid(entry.original_assessment)
      && entry.after.resolution_type !== "accept_majority" && recordedLevelScoreConsistent(entry.after)
      && originalAssessmentKey(entry.original_assessment) === originalAssessmentKey(context.original_assessment))) && entry.check_run_id === context.check_run_id
    && entry.criterion_id === context.criterion_id && entry.check_result_id === context.check_result_id
    && isAssessmentRevision(entry.assessment_revision_before)
    && entry.assessment_revision_after === entry.assessment_revision_before + 1
    && entry.assessment_revision_after <= context.assessment_revision
    && snapshotValid(entry.after) && entry.after.provenance === "recorded"
    && entry.after.actor_id === entry.performed_by && entry.after.recorded_at === entry.performed_at
    && entry.after.judgment_revision <= (context.current?.judgment_revision ?? -1)
    && (entry.action === "initial_resolution" || entry.action === "judgment_recorded" ? entry.before === null && entry.after.judgment_revision === 1
      : entry.action === "judgment_revised" && snapshotValid(entry.before)
        && entry.after.judgment_revision === entry.before.judgment_revision + 1));
}
