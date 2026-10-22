import { createContext, useContext } from "react";
import type { EscalatedItemOut, EscalationResolution, ResolveEscalationOut } from "../api/types";

export interface ReviewDraft {
  criterionId: number;
  resolution: EscalationResolution;
  level?: number;
  reason: string;
  attempted: boolean;
  saving: boolean;
  error?: unknown;
}
export interface ReviewCompletion { version: number; message: string; focusFrom: Element | null }
export interface ReviewScope {
  checkRunId: number;
  drafts: ReadonlyMap<number, ReviewDraft>;
  completion?: ReviewCompletion;
  choose: (item: EscalatedItemOut, resolution: EscalationResolution, level?: number) => void;
  changeReason: (id: number, reason: string) => void;
  cancel: (id: number) => void;
  confirm: (id: number, message: (out: ResolveEscalationOut) => string) => void;
  reconcile: (items: EscalatedItemOut[]) => void;
}
export const ReviewContext = createContext<ReviewScope | null>(null);

export function useCriterionReviewScope(checkRunId: number) {
  const scope = useContext(ReviewContext);
  if (!scope || scope.checkRunId !== checkRunId) throw new Error("Criterion review requires its report route owner.");
  return scope;
}
