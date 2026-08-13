// Single source of truth for how a set of flags maps to a panel-level
// tone (BUG-033) — same "one shared place decides, never a per-component
// judgment call" precedent as readinessTone.ts.
import type { FlagSummaryOut } from "../api/types";
import type { StatusPillTone } from "../components/StatusPill";

/** Worst UNRESOLVED severity present drives the panel's tone; a run
 * where every flag has already been overridden reads as settled
 * ("neutral"), never as if it still carries live risk. */
export function worstFlagTone(flags: FlagSummaryOut[]): StatusPillTone {
  const unresolved = flags.filter((f) => !f.overridden);
  if (unresolved.some((f) => f.severity === "high")) return "danger";
  if (unresolved.some((f) => f.severity === "med")) return "caution";
  if (unresolved.some((f) => f.severity === "low")) return "info";
  return "neutral";
}
