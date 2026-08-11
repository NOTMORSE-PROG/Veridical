// Single source of truth for how a readiness verdict maps to a StatusPill
// tone (V-056). Before this file existed, KpiCards.tsx and Report.tsx each
// picked their own tone for the same verdict string and disagreed —
// "Conditionally Ready" rendered info-blue on the dashboard and
// attention-amber on the report it links to, one click apart (Nielsen
// heuristic #4, found live). Both screens now import this map instead of
// choosing independently.
import type { StatusPillTone } from "../components/StatusPill";
import type { ReportOut } from "../api/types";

export const READINESS_TONE: Record<ReportOut["status"], StatusPillTone> = {
  ready: "success",
  conditionally_ready: "caution",
  not_ready: "danger",
  needs_review: "neutral",
};

export const READINESS_LABEL: Record<ReportOut["status"], string> = {
  ready: "Ready",
  conditionally_ready: "Conditionally Ready",
  not_ready: "Not Ready",
  needs_review: "Needs Review",
};
