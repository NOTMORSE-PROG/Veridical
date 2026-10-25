import { REVIEW_DESK_RETURN } from "../config/signalRoutes";
import { criterionRecordId } from "./savedJudgment";

export function criterionSourceReturn(state: unknown, run: number) {
  if (!state || typeof state !== "object" || Array.isArray(state) || !Number.isSafeInteger(run) || run <= 0) return;
  const saved = "criterionRecordReturn" in state;
  const pending = "pendingCriterionReturn" in state;
  if (saved === pending) return;
  const value = "criterionRecordReturn" in state ? state.criterionRecordReturn : "pendingCriterionReturn" in state ? state.pendingCriterionReturn : undefined;
  if (!value || typeof value !== "object" || Array.isArray(value) || !("returnPath" in value) || !("elementId" in value)) return;
  const { returnPath, elementId } = value;
  if (typeof returnPath !== "string" || returnPath.length > REVIEW_DESK_RETURN.maxPathLength
    || /[\\#\p{Cc}]/u.test(returnPath) || returnPath.split("?")[0] !== `/report/${run}` || typeof elementId !== "string") return;
  const returnState = Object.fromEntries(Object.entries(state).filter(([key]) => key !== "criterionRecordReturn" && key !== "pendingCriterionReturn"));
  if (pending) {
    const action = pendingCriterionSourceAction(elementId, run);
    if (!action || !("kind" in value) || value.kind !== "pending" || !("checkResultId" in value) || value.checkResultId !== action.checkResultId) return;
    return { kind: "pending" as const, returnPath, elementId, checkResultId: action.checkResultId, state: returnState, retainOnSearchChange: true,
      fallbackElementId: [`signal-resolution-heading-${action.checkResultId}`, "escalated-heading", "readiness-report-heading"] };
  }
  const action = elementId.match(/^signal-saved-source-(\d+)-(\d+)-(\d+)$/);
  const criterionId = action && Number(action[2]);
  if (!action || Number(action[1]) !== run || !Number.isSafeInteger(criterionId) || !criterionId || criterionId < 0 || !Number.isSafeInteger(Number(action[3]))) return;
  return { kind: "saved" as const, returnPath, elementId, criterionId, state: returnState, retainOnSearchChange: true, fallbackElementId: criterionRecordId(run, criterionId) };
}

export function pendingCriterionSourceAction(elementId: string | undefined, run: number) {
  const action = elementId?.match(/^signal-resolution-(?:source|coverage)-(\d+)-(\d+)-(\d+)$/);
  if (!action || Number(action[1]) !== run || !Number.isSafeInteger(run) || run <= 0) return;
  const checkResultId = Number(action[2]);
  if (!Number.isSafeInteger(checkResultId) || checkResultId <= 0 || !Number.isSafeInteger(Number(action[3]))) return;
  return { elementId: elementId!, checkResultId };
}
