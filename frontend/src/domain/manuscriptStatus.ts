// Single source of truth for a manuscript row's PIPELINE status pill
// (queued/running/done/failed, plus a real DECISION once one exists,
// V-038) -- extracted from ManuscriptsTable.tsx (its original home) so
// RerunModal.tsx (V-041) doesn't fork a second copy of the same
// tone/label logic. The READINESS verdict (Ready/Not Ready/etc.) is
// deliberately NOT part of this -- it lives on the report itself
// (screen 4h), never duplicated here.
import type { ManuscriptListItem } from "../api/types";
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

export function manuscriptStatus(row: ManuscriptListItem): { label: string; tone: StatusPillTone } {
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
