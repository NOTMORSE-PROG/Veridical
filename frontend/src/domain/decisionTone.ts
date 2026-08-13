// Single source of truth for how a terminal decision (V-038, F8.5) maps
// to a StatusPill tone/label -- same "one map, many consumers" precedent
// readinessTone.ts already established, so DecisionPanel/Report/
// ManuscriptsTable can never independently drift on what "Approved"
// looks like.
import type { Decision } from "../api/types";
import type { StatusPillTone } from "../components/StatusPill";

export const DECISION_TONE: Record<Decision, StatusPillTone> = {
  approved: "success",
  returned: "caution",
  rejected: "danger",
};

export const DECISION_LABEL: Record<Decision, string> = {
  approved: "Approved for defense",
  returned: "Returned for revision",
  rejected: "Rejected",
};
