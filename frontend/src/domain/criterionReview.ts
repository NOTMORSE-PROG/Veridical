import type { EscalatedItemOut, EscalationResolution } from "../api/types";

export const resolutionRequiresReason = (resolution: EscalationResolution) =>
  resolution === "needs_document" || resolution === "accept_majority";

export function recordedInstructorComment(resolution: unknown, comment: unknown): string | undefined {
  if (typeof comment !== "string") return;
  if (comment.trim()) return comment;
  if (["mark_pass", "mark_fail", "mark_level"].includes(resolution as string)) return "No comment recorded.";
}

export const REVIEW_DRAFT_COPY = {
  retention: "Your unsaved choice and any notes stay available while you review this report and its manuscript.",
  saving: "Saving this resolution. You can inspect the manuscript while it finishes.",
} as const;

export const CRITERION_EXCLUSION_COPY = {
  action: "Exclude: needs another document",
  hint: "This criterion stays ungraded and does not count toward the readiness calculation. Saving resolves this review item.",
  wait: "To wait for more evidence, leave the criterion unresolved.",
  consequence: "When saved, this criterion stays ungraded and outside the readiness calculation, and this review item is resolved. It is not marked as met. Other review requirements still apply.",
  cancel: "Cancel to keep this criterion unresolved while you gather evidence.",
  reason: "Reason for exclusion (required)",
  confirm: "Confirm exclusion",
  success: "Exclusion recorded without a grade. The readiness report has been updated.",
  recorded: "Excluded from readiness calculation: needs another document",
  noGrade: "No grade recorded.",
} as const;

export const CRITERION_REVIEW_COPY = {
  judgmentNeeded: "Your judgment is needed",
  reviewSavedPassages: "No grade was accepted for this criterion. Review the saved passages and level descriptions, then choose a grade.",
  reviewManuscript: "No grade was accepted for this criterion. Review the manuscript and level descriptions, then choose a grade.",
  supportIncomplete: "The separate evidence review did not finish.",
  supportUnsupported: "The separate evidence review did not support the suggested grade.",
  supportUnconfirmed: "The separate evidence review could not confirm that the evidence supports the suggested grade.",
  sourceInstructions: "Text that may be directed at automated grading needs review.",
  unrecognizedGrade: "The recorded suggestion is not a recognized grade for this criterion.",
  notGraded: "A completed grade is not available in this review record.",
  tentativeSuggestion: "A tentative AI suggestion is recorded. Review it against the criterion and manuscript.",
  details: "the grading passes",
  detailsTitle: "Automatic grading passes",
  detailsQualification: "Each pass is one automatic grading attempt on this criterion. Passes can disagree; a pass is not an independent reviewer.",
  entriesLabel: "Automatic grading passes",
  missingEntry: "No grade was recorded for this pass.",
  noEntries: "No comparison entries are present in this record.",
} as const;

export interface CriterionReviewNotice {
  kind: "support" | "source_instructions" | "unrecognized_grade" | "not_graded" | "tentative_suggestion";
  text: string;
}

/** Recorded limitations can coexist; result slots do not establish their cause. */
export function criterionReviewNotices(item: EscalatedItemOut): CriterionReviewNotice[] {
  const notices: CriterionReviewNotice[] = [];
  if (item.support_withheld || item.review_reason === "support_incomplete" || item.review_reason === "support_unconfirmed") {
    notices.push({
      kind: "support",
      text: item.support_status === "incomplete" || item.review_reason === "support_incomplete"
        ? CRITERION_REVIEW_COPY.supportIncomplete
        : item.support_status === "unsupported"
          ? CRITERION_REVIEW_COPY.supportUnsupported
          : CRITERION_REVIEW_COPY.supportUnconfirmed,
    });
  }
  if (item.injection_suspected || item.review_reason === "injection_suspected") {
    notices.push({ kind: "source_instructions", text: CRITERION_REVIEW_COPY.sourceInstructions });
  }
  if (item.verdict_unrecognized) {
    notices.push({ kind: "unrecognized_grade", text: CRITERION_REVIEW_COPY.unrecognizedGrade });
  }
  if (item.review_reason === "not_graded") {
    notices.push({ kind: "not_graded", text: CRITERION_REVIEW_COPY.notGraded });
  }
  if (notices.length === 0 && item.ai_majority_verdict?.trim()) {
    notices.push({ kind: "tentative_suggestion", text: CRITERION_REVIEW_COPY.tentativeSuggestion });
  }
  return notices;
}

// Stored reasons written in internal terms, said the way the screens say them.
export const PLAIN_REASON_WORDING: readonly (readonly [string, string])[] = [
  ["The self-consistency vote reached no majority.", "The grading passes did not agree on a grade."],
];
