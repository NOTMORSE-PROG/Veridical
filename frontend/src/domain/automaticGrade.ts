// F3/F8 screen 4h: how an automatic grade was chosen, shown beside the grade
// until an instructor changes it (owner decision 2026-10-29).
import type { CriterionResultOut } from "../api/types";

export const AUTOMATIC_GRADE_COPY = {
  agreed: "Graded automatically: {agreeing} of {passes} grading passes chose this grade.",
  middle_of_three: "Graded automatically: the three grading passes disagreed, so the middle level was used.",
  lower_of_two: "Graded automatically: the deciding pass could not be checked against the manuscript, so the lower of the two checked grades was used.",
  single_verified_pass: "Graded automatically: only one grading pass quoted the manuscript in a way that could be checked, so its grade was used.",
  caution: "Evidence check not confirmed: {reason}",
  allRecorded: "Every criterion has a recorded outcome. Automatic grades are listed under Read saved grades, where Change grade sets a different level. Continue to the integrity signals.",
  confirmUnchanged: "Recording this decision also confirms the {count} automatic grade(s) you did not change.",
  change: "Change grade",
  changeAgain: "Change grade again",
} as const;

type AutomaticGradeRow = Pick<CriterionResultOut, "decision_basis" | "support_caution" | "agreeing_passes" | "grading_passes">;

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_match, key: string) => String(values[key]));

export function automaticGradeLines(row: AutomaticGradeRow): string[] {
  const lines: string[] = [];
  if (row.decision_basis) lines.push(AUTOMATIC_GRADE_COPY[row.decision_basis]);
  else if (row.grading_passes && row.agreeing_passes) {
    lines.push(fill(AUTOMATIC_GRADE_COPY.agreed, { agreeing: row.agreeing_passes, passes: row.grading_passes }));
  }
  if (lines.length && row.support_caution?.trim()) {
    lines.push(fill(AUTOMATIC_GRADE_COPY.caution, { reason: row.support_caution.trim() }));
  }
  return lines;
}

export function fillAutomaticConfirmation(count: number): string {
  return fill(AUTOMATIC_GRADE_COPY.confirmUnchanged, { count });
}
