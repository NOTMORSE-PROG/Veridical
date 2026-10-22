import type { ReportOut } from "../api/types";
import { ApiError } from "../api/client";

export const REPORT_READ_REQUIRED = "Reload the report before making or reopening a final decision.";

export function recoverableReadError(error: unknown) {
  return !(error instanceof ApiError && error.status >= 400 && error.status < 500);
}

// Retained data is useful only for the same run and a transient read failure.
// Permission, missing-record and other client failures keep the existing gate.
export function usableReport(report: ReportOut | undefined, checkRunId: number, error: unknown) {
  return report?.check_run_id === checkRunId && recoverableReadError(error)
    ? report : undefined;
}

