import type { EscalatedItemOut, ReportOut } from "../api/types";
import { isAssessmentRevision } from "./assessmentRevision";

/** Join separately fetched records only within the same saved assessment. */
export function pendingAssessmentRow(report: ReportOut | undefined, item: EscalatedItemOut, checkRunId: number) {
  if (!report || report.check_run_id !== checkRunId
    || !isAssessmentRevision(report.assessment_revision)
    || !isAssessmentRevision(item.assessment_revision)
    || report.assessment_revision !== item.assessment_revision) return;
  const rows = report.results.filter(row => row.criterion_id === item.criterion_id || row.check_result_id === item.check_result_id);
  if (rows.length !== 1) return;
  const row = rows[0];
  if (row.criterion_id !== item.criterion_id || row.check_result_id !== item.check_result_id || row.resolution
    || !["escalated", "unverifiable", "api_down", "quota_exhausted"].includes(row.outcome)) return;
  return row;
}
