import { createContext, useContext } from "react";
import type { Decision, EscalatedItemOut, EscalationResolution, ResolveEscalationOut, ResultRowCommon } from "../api/types";
import type { FocusContinuation } from "../routing/focusContinuation";
import type { AssessmentNotice } from "../domain/reportAssessmentContext";
import type { RevisionScope } from "./useJudgmentRevisions";

export interface ReviewDraft {
  criterionId: number;
  criterionText: string;
  expectedRevision: number;
  frozen?: boolean;
  reviewedRevision?: number;
  currentResolved?: boolean;
  needsChoice?: boolean;
  resolution: EscalationResolution;
  level?: number;
  reason: string;
  attempted: boolean;
  saving: boolean;
  // A transport failure cannot establish whether this submitted revision committed.
  uncertainRevision?: number;
  error?: unknown;
}
export interface ReviewCompletion {
  version: number;
  message: string;
  focusFrom: Element | null;
  result: ResultRowCommon;
  focus?: FocusContinuation;
}
export interface ReviewScope {
  checkRunId: number;
  decision: Decision | null | undefined;
  assessmentNotices?: AssessmentNotice[];
  drafts: ReadonlyMap<number, ReviewDraft>;
  completion?: ReviewCompletion;
  confirmed: ReadonlyMap<number, ResultRowCommon>;
  savedNavigation?: { version: number; criterionId?: number; owner: Element | null };
  viewSaved: (criterionId: number | undefined, trigger: HTMLElement) => void;
  choose: (item: EscalatedItemOut, resolution: EscalationResolution, level?: number) => void;
  changeReason: (id: number, reason: string) => void;
  cancel: (id: number) => void;
  confirm: (id: number, message: (out: ResolveEscalationOut) => string) => void;
  reconcile: (items: EscalatedItemOut[]) => void;
  isDraftCurrent: (draft: ReviewDraft) => boolean;
  canUseCurrentDraft: (draft: ReviewDraft) => boolean;
  canChoose: boolean;
  revisions: RevisionScope;
  reviewCurrent: (id: number) => void;
  useCurrentDraft: (id: number) => void;
}
export const ReviewContext = createContext<ReviewScope | null>(null);

export function useCriterionReviewScope(checkRunId: number) {
  const scope = useContext(ReviewContext);
  if (!scope || scope.checkRunId !== checkRunId) throw new Error("Criterion review requires its report route owner.");
  return scope;
}
