export const SEMANTIC_SUPPORT_REVIEW = {
  title: "AI evidence support needs review",
  body: "The saved record does not confirm that every AI criterion result completed the current evidence-support checks. Review each result's status, any available explanation and the manuscript before relying on AI content. Recorded outcomes and instructor decisions are preserved.",
  shared: "Ask the instructor for access to the manuscript if needed.",
} as const;

export const RECORDED_GRADE_BASIS = {
  qualification: "A recorded location does not establish support for a grade.",
  review: "Review the criterion, any recorded explanation and the manuscript before choosing a grade.",
  missingExplanation: "No AI explanation is available for this review item.",
  missingExcerpt: "No located excerpt is available for this review item.",
  missingLocation: "No manuscript location is available for this excerpt.",
} as const;
