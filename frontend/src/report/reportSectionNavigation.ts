import type { MouseEvent } from "react";
import type { Location } from "react-router";
import { rememberRouteReturnFocus } from "../routing/useRouteFocus";

export const REPORT_SECTION_TARGETS = ["review-criteria", "criteria-results-heading", "coverage-statement-heading"] as const;

/** Preserve an explicit section intent when a workspace opens its full report. */
export function focusReportSection(event: MouseEvent<HTMLAnchorElement>, location: Pick<Location, "pathname" | "search">, targetId: typeof REPORT_SECTION_TARGETS[number]) {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link = event.currentTarget;
  if (link.pathname !== location.pathname) {
    rememberRouteReturnFocus(link.pathname + link.search, location.pathname + location.search, targetId, {
      armed: true, fallbackElementId: "readiness-report-heading", scrollBlock: "start",
    });
    return;
  }
  requestAnimationFrame(() => {
    const target = document.getElementById(targetId);
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
  });
}

// A link to another manuscript review step changes only the query, so the
// target section mounts on the next render; wait for it before moving focus.
export function focusAfterStepChange(event: MouseEvent<HTMLAnchorElement>, targetId: string) {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const target = document.getElementById(targetId);
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
  }));
}
