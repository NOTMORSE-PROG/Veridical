// F8, screens 4h/4w: saved records use actual run and criterion identities.
import type { EscalationResolution, ResolveEscalationOut, ResultRowCommon } from "../api/types";
import { resultDisplay } from "./ResultsTable";
import { CRITERION_SCORE_UNITS } from "../config/ui";

export const SAVED_CRITERION_PARAM = "criterion_record";
export const criterionRecordId = (run: number, criterion: number) => `criterion-record-${run}-${criterion}`;

export function confirmedJudgment(out: ResolveEscalationOut, criterionId: number,
  submitted: { resolution: EscalationResolution; reason: string; level?: number }): ResultRowCommon | undefined {
  const rows = out.report.results.filter((row) => row.criterion_id === criterionId);
  const row = rows.length === 1 ? rows[0] : undefined;
  if (!row || typeof row.text !== "string" || row.outcome !== out.outcome || row.score !== out.score
    || !(row.score === null || Number.isFinite(row.score))
    || row.resolution?.type !== submitted.resolution || row.resolution.reason !== submitted.reason) return;
  if (submitted.resolution === "needs_document") {
    if (row.outcome !== "not_applicable" || row.score !== null || row.level != null) return;
  } else {
    if (row.score === null || row.score < CRITERION_SCORE_UNITS.none || row.score > CRITERION_SCORE_UNITS.full
      || (row.outcome !== "passed" && row.outcome !== "failed")) return;
    if (row.level) {
      if (row.outcome !== "passed" || !row.level.name.trim() || !Number.isFinite(row.level.points)
        || !Number.isFinite(row.level.max_points) || row.level.max_points <= 0
        || row.level.points < 0 || row.level.points > row.level.max_points) return;
    } else if ((row.outcome === "failed" && row.score !== CRITERION_SCORE_UNITS.none)
      || (row.outcome === "passed" && row.score <= CRITERION_SCORE_UNITS.none)) return;
    if (submitted.resolution === "mark_level" && (!row.level || row.level.ordinal !== submitted.level)) return;
    if (submitted.resolution === "mark_pass" && (row.outcome !== "passed" || row.score !== CRITERION_SCORE_UNITS.full || row.level != null)) return;
    if (submitted.resolution === "mark_fail" && (row.outcome !== "failed" || row.score !== CRITERION_SCORE_UNITS.none || row.level != null)) return;
  }
  return row;
}

export function sameSavedJudgment(row: ResultRowCommon, saved: ResultRowCommon) {
  return row.criterion_id === saved.criterion_id && row.outcome === saved.outcome && row.score === saved.score
    && row.level?.ordinal === saved.level?.ordinal && row.level?.name === saved.level?.name
    && row.resolution?.type === saved.resolution?.type && row.resolution?.reason === saved.resolution?.reason;
}

export function revealCriterionRecord(target: HTMLElement) {
  let disclosure = target.parentElement?.closest("details");
  while (disclosure) { disclosure.open = true; disclosure = disclosure.parentElement?.closest("details"); }
}

export function recordedGradeLabel(row: ResultRowCommon) {
  if (row.level?.name?.trim()) return row.level.name;
  if (row.resolution?.type === "mark_level") return "Recorded grade unavailable";
  if (row.outcome === "passed") return resultDisplay(row).tone === "caution" ? "Partly meets criterion" : "Meets criterion";
  if (row.outcome === "failed") return "Does not meet";
  return "Recorded grade unavailable";
}

