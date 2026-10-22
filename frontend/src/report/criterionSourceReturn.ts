import { REVIEW_DESK_RETURN } from "../config/signalRoutes";
import { criterionRecordId } from "./savedJudgment";

export function criterionSourceReturn(state: unknown, run: number) {
  if (!state || typeof state !== "object" || !("criterionRecordReturn" in state)) return;
  const value = state.criterionRecordReturn;
  if (!value || typeof value !== "object" || !("returnPath" in value) || !("elementId" in value)) return;
  const { returnPath, elementId } = value;
  if (typeof returnPath !== "string" || returnPath.length > REVIEW_DESK_RETURN.maxPathLength
    || /[\\#\p{Cc}]/u.test(returnPath) || returnPath.split("?")[0] !== `/report/${run}` || typeof elementId !== "string") return;
  const action = elementId.match(/^signal-saved-source-(\d+)-(\d+)-\d+$/);
  const criterionId = action && Number(action[2]);
  if (!action || Number(action[1]) !== run || !Number.isSafeInteger(criterionId) || !criterionId || criterionId < 0) return;
  return { returnPath, elementId, criterionId, state: Object.fromEntries(Object.entries(state).filter(([key]) => key !== "criterionRecordReturn")), fallbackElementId: criterionRecordId(run, criterionId) };
}
