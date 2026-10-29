import type { ReportOut } from "../api/types";

type ModuleState = NonNullable<ReportOut["integrity_modules"]>[number]["state"] | "not_recorded";

// Shared by the readiness report and manuscript review so an empty check
// says the same honest thing on both screens.
export const MODULE_EMPTY_STATE: Record<ModuleState, { title: string; description: string }> = {
  completed: { title: "No findings to review", description: "No findings were recorded for this check. This does not establish that the whole manuscript passed." },
  not_applicable: { title: "Not applicable to this manuscript", description: "This check was marked not applicable. There are no findings to review in this category." },
  partial: { title: "Check partly completed", description: "Only part of this check was completed. No findings were recorded. Read the assessment limits before drawing a conclusion." },
  incomplete: { title: "Check not fully run", description: "This check was not fully run. No findings are available. Read the assessment limits to see what was checked." },
  not_recorded: { title: "No saved check status", description: "This report does not say whether this check ran. An empty list does not mean the manuscript passed." },
};

export function moduleEmptyState(report: Pick<ReportOut, "integrity_modules"> | undefined, kind: string) {
  const state = report?.integrity_modules?.find(module => module.check_kind === kind)?.state ?? "not_recorded";
  return MODULE_EMPTY_STATE[state as ModuleState] ?? MODULE_EMPTY_STATE.not_recorded;
}
