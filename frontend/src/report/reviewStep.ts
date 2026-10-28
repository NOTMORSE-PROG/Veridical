// F8/4h: URL-selected presentation, never a completion or scoring state.
export const REPORT_STEPS = [
  { id: "criteria", target: "review-criteria", label: "Review criteria" },
  { id: "findings", target: "integrity-signals", label: "Inspect findings" },
  { id: "grades", target: "criteria-results-heading", label: "Read saved grades" },
  { id: "decision", target: "final-decision", label: "Make decision" },
] as const;
export type ReportStep = typeof REPORT_STEPS[number]["id"];

export function reportStep(search: string, hash: string): ReportStep {
  const target = hash.replace(/^#/, "");
  const exact = REPORT_STEPS.find(step => step.target === target);
  if (exact) return exact.id;
  if (/^(?:criterion-record-|judgment-revision-|signal-saved-source-)/.test(target)) return "grades";
  if (/^(?:signal-resolution-|signal-criterion-open-)/.test(target)) return "criteria";
  if (/^comparison-lead-/.test(target)) return "findings";
  const requested = new URLSearchParams(search).get("review_step");
  return REPORT_STEPS.find(step => step.id === requested)?.id ?? "criteria";
}
