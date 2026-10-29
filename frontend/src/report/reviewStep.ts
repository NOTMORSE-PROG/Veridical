// F8/4h/4w: URL-selected presentation, never a completion or scoring state.
export const REPORT_STEPS = [
  { id: "criteria", target: "review-criteria", label: "Review criteria" },
  { id: "findings", target: "integrity-signals", label: "Inspect findings" },
  { id: "grades", target: "criteria-results-heading", label: "Check recorded grades" },
  { id: "decision", target: "final-decision", label: "Record decision" },
] as const;
export type ReportStep = typeof REPORT_STEPS[number]["id"];

export const WORKSPACE_STEP_PARAM = "step";

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

function namedStep(value: string | null): ReportStep | null {
  return REPORT_STEPS.find(step => step.id === value)?.id ?? null;
}

// The manuscript review workspace shows one step at a time. Finding, match
// and filter addresses always belong to the findings step; an address that
// names no step opens on the first one that still needs the instructor.
export function workspaceStep(params: URLSearchParams, work: { pendingCriteria: number; openFindings: number } | undefined): ReportStep | null {
  const view = params.get("view");
  if (view === "queue" || view === "finding" || view === "matches" || params.get("panel") === "explore") return "findings";
  // A decision-view address keeps its step even if it still carries a stale
  // finding selection; only finding addresses without one open the finding.
  if (view !== "decision" && (params.has("finding") || params.has("flag"))) return "findings";
  // Opening saved records or one criterion is a more specific request than
  // the step the instructor was on when they asked for it.
  if (params.has("criterion_record")) return "grades";
  if (params.has("criterion")) return "criteria";
  const named = namedStep(params.get(WORKSPACE_STEP_PARAM));
  if (named) return named;
  if (!work) return null;
  if (work.pendingCriteria > 0) return "criteria";
  if (view !== "decision" && work.openFindings > 0) return "findings";
  return "decision";
}

export function workspaceStepPath(checkRunId: number, step: ReportStep, hash?: string): string {
  const search = step === "findings" ? "view=queue&pane=review" : `view=decision&${WORKSPACE_STEP_PARAM}=${step}&pane=review`;
  return `/report/${checkRunId}/document?${search}${hash ? `#${hash}` : ""}`;
}
