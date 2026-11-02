import type { CriterionResultOut } from "../api/types";
import { automaticGradeCaution } from "./automaticGrade";
import summaryCopy from "../../../backend/app/report/data/judgment_summary_copy.json";

export const JUDGMENT_SUMMARY_COPY = summaryCopy;

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_match, key: string) => String(values[key]));

// Automatic grades the instructor has not changed that carry a caution or a
// later note: the same rule that puts the warning chip on a card. Escalated
// rows have no automatic-grade lines, so they are never counted twice.
export function checkFirstRows(rows: CriterionResultOut[]): CriterionResultOut[] {
  return rows.filter(row => !row.resolution && automaticGradeCaution(row) !== null);
}

const counted = (count: number, none: string, one: string, many: string) =>
  count === 0 ? none : count === 1 ? one : fill(many, { count });

export function judgmentSummary(grade: number, check: number) {
  const copy = JUDGMENT_SUMMARY_COPY;
  return {
    kicker: grade > 0 ? copy.kicker_grade : check > 0 ? copy.kicker_check : copy.kicker_none,
    badge: fill(copy.badge, { grade, check }),
    grade: counted(grade, copy.grade_none, copy.grade_one, copy.grade_many),
    gradeHint: grade === 1 ? copy.grade_hint_one : copy.grade_hint_many,
    check: counted(check, copy.check_none, copy.check_one, copy.check_many),
    checkHint: check === 1 ? copy.check_hint_one : copy.check_hint_many,
    kpiLinkLabel: fill(copy.kpi_link_label, { count: check }),
  };
}
