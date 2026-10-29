// V-089: words for bulk confirm and dismiss.
import { BULK_RESOLVE_MAX_LOCATIONS, BULK_RESOLVE_MAX_TEXT_CHARS } from "../config/ui";
import type { BulkAction } from "../report/useBulkResolve";

export const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

export const BULK_REVIEW_COPY = {
  selected: (findings: number, locations: number) => `${plural(findings, "finding", "findings")} selected${locations !== findings ? ` (${plural(locations, "location", "locations")})` : ""}`,
  tooMany: (locations: number) => `${plural(locations, "location is", "locations are")} selected. One action takes at most ${BULK_RESOLVE_MAX_LOCATIONS}; narrow the selection.`,
  confirm: "Confirm as real issues",
  dismiss: "Dismiss",
  clear: "Clear selection",
  selectPage: "Select all on this page",
  selectCheck: "Select all in this check",
  decided: "This report has a recorded decision. Reopen it to change findings.",
  confirmTitle: (count: number) => `Confirm ${plural(count, "finding", "findings")} as real issues?`,
  dismissTitle: (count: number) => `Dismiss ${plural(count, "finding", "findings")}?`,
  confirmCounts: "Confirmed findings stay counted for readiness and show as confirmed, not open.",
  dismissCounts: "Dismissed findings no longer count for readiness. Each one keeps your reason in its record and in Audit.",
  unopened: (count: number) => count === 0 ? "You have opened every selected finding." : `${plural(count, "selected finding has", "selected findings have")} not been opened yet.`,
  reopens: (count: number) => `${plural(count, "selected finding was", "selected findings were")} dismissed; confirming reopens ${count === 1 ? "it" : "them"} as a real issue.`,
  sourceConfirmed: (count: number) => `${plural(count, "finding is", "findings are")} left out: a confirmed citation source cannot also be confirmed as a real issue.`,
  more: (count: number) => `and ${count} more`,
  note: "Comment (optional)",
  noteHint: "Copied to each confirmed finding.",
  reason: "Reason for dismissing (required)",
  reasonHint: "Copied to each dismissed finding. Say what you checked.",
  reasonMissing: "Enter a reason before dismissing.",
  counter: (used: number) => `${used} of ${BULK_RESOLVE_MAX_TEXT_CHARS} characters`,
  tooLong: `Keep it to ${BULK_RESOLVE_MAX_TEXT_CHARS} characters.`,
  cancel: "Cancel",
  done: (action: BulkAction, changed: number, unchanged: number) => {
    const verb = action === "confirm" ? "confirmed" : "dismissed";
    const head = `${plural(changed, "finding", "findings")} ${verb}`;
    return unchanged === 0 ? `${head}.` : `${head} · ${plural(unchanged, "was", "were")} already ${verb} and ${unchanged === 1 ? "was" : "were"} left unchanged.`;
  },
} as const;
