// V-077: whether an originality result leaves "no integrity signals" unable
// to read as success. Kept apart from the component (fast-refresh rule).
import type { OriginalityScope } from "../api/types";

/** True when the originality check had nothing to compare against, or its
 * count was not recorded (charter judgment 9: N/A is not "passed"). */
export function originalityUnassessed(scope: OriginalityScope | null | undefined): boolean {
  return Boolean(
    scope
      && (scope.not_compared_reason === "no_text" || scope.compared_count === null || scope.compared_count === 0),
  );
}
