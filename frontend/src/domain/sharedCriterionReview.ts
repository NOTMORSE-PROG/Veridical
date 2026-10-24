import type { PublicCriterionResultOut } from "../api/types";

// F8.7: mirror the server's existing criterion-review membership using only
// current public outcomes. Flags and assessment limitations are separate units.
const UNRESOLVED_CRITERION_OUTCOMES: ReadonlySet<PublicCriterionResultOut["outcome"]> = new Set([
  "escalated", "unverifiable", "quota_exhausted", "api_down",
]);

export function isUnresolvedSharedCriterion(row: Pick<PublicCriterionResultOut, "outcome">): boolean {
  return UNRESOLVED_CRITERION_OUTCOMES.has(row.outcome);
}
