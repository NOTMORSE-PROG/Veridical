import type { ResultRowCommon } from "../api/types";
import { criterionAssessmentDetails } from "./criterionAssessment";
import ruleFactsCopy from "../../../backend/app/report/data/rule_facts_copy.json";

export const RULE_SCOPE_QUALIFICATION = ruleFactsCopy.scope_qualification;

export function unconfirmedAutomaticRuleRows(rows: ResultRowCommon[], publicView = false) {
  return rows.filter(row => {
    const assessment = criterionAssessmentDetails(row, publicView);
    return assessment.ruleScopeUnconfirmed && !assessment.instructorRecorded
      && (row.outcome === "passed" || row.outcome === "failed");
  });
}
