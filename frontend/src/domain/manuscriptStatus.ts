// Single source of truth for a manuscript row's PIPELINE status pill
// (queued/running/done/failed, plus a real DECISION once one exists,
// V-038) -- extracted from ManuscriptsTable.tsx (its original home) so
// RerunModal.tsx (V-041) doesn't fork a second copy of the same
// tone/label logic. The READINESS verdict (Ready/Not Ready/etc.) is
// deliberately NOT part of this -- it lives on the report itself
// (screen 4h), never duplicated here.
//
// V-071 (BUG-058): the old Archive.tsx (retired V-066, folded into the
// Library screen) used to carry its own second copy of this logic that
// never read ingest_status at all, so an ingestion-failed manuscript
// showed as "Not checked yet" there while this same function correctly
// called it "Ingestion failed" on the dashboard -- two screens disagreeing
// about the same file. The parameter type below is a structural subset
// `ManuscriptListItem` satisfies, not the full dashboard-only shape.
import type { CheckRunStatus, Decision } from "../api/types";
import type { StatusPillTone } from "../components/StatusPill";
import { DECISION_LABEL, DECISION_TONE } from "./decisionTone";

export const RUNNING_STATUSES = new Set([
  "queued",
  "ingesting",
  "structural",
  "semantic",
  "integrity",
  "aggregating",
]);

interface ManuscriptStatusInput {
  ingest_status: string;
  latest_check_run_status: CheckRunStatus | null;
  latest_decision?: Decision | null;
}

export function manuscriptStatus(row: ManuscriptStatusInput): { label: string; tone: StatusPillTone } {
  if (row.latest_check_run_status === "done") {
    if (row.latest_decision) {
      return { label: DECISION_LABEL[row.latest_decision], tone: DECISION_TONE[row.latest_decision] };
    }
    return { label: "Checked", tone: "success" };
  }
  if (row.latest_check_run_status === "failed") return { label: "Check failed", tone: "attention" };
  if (row.latest_check_run_status && RUNNING_STATUSES.has(row.latest_check_run_status)) {
    return { label: "Checking", tone: "info" };
  }
  if (row.ingest_status === "done") return { label: "Not checked yet", tone: "neutral" };
  if (row.ingest_status === "failed") return { label: "Ingestion failed", tone: "attention" };
  return { label: "Ingesting", tone: "info" };
}
