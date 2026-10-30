import { REVIEW_DESK_RETURN } from "../config/signalRoutes";

// BUG-541/542: a finding opened from the report's findings tab carries the
// report view it came from and the Review evidence link that opened it, so
// the way back returns to that exact view with focus on that finding.
export function findingSourceReturn(state: unknown, run: number) {
  if (!state || typeof state !== "object" || Array.isArray(state) || !Number.isSafeInteger(run) || run <= 0) return;
  if (!("routeReturnFocus" in state)) return;
  const value = state.routeReturnFocus;
  if (!value || typeof value !== "object" || Array.isArray(value) || !("returnPath" in value) || !("elementId" in value)) return;
  const { returnPath, elementId } = value;
  if (typeof returnPath !== "string" || returnPath.length > REVIEW_DESK_RETURN.maxPathLength
    || /[\\#\p{Cc}]/u.test(returnPath) || returnPath.split("?")[0] !== `/report/${run}`) return;
  if (typeof elementId !== "string" || !/^signal-flag-review-(?:summary-)?\d+$/.test(elementId)) return;
  const returnState = Object.fromEntries(Object.entries(state).filter(([key]) => key !== "routeReturnFocus"));
  return { returnPath, elementId, state: returnState, retainOnSearchChange: true };
}
