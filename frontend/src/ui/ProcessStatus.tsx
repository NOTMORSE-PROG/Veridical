import type { CheckRunStatus } from "../api/types";

type ProcessStatusValue = CheckRunStatus | "not_checked" | "upload_failed" | "preparing";

const PROCESS_LABELS: Record<ProcessStatusValue, string> = {
  not_checked: "Ready to check",
  upload_failed: "Upload needs attention",
  preparing: "Preparing manuscript",
  queued: "Queued to check",
  ingesting: "Preparing manuscript",
  structural: "Checking structure",
  semantic: "Checking criteria",
  integrity: "Checking integrity",
  aggregating: "Preparing report",
  done: "Check complete",
  failed: "Check needs attention",
  cancelled: "Check cancelled",
};

export function ProcessStatus({ status }: { status: ProcessStatusValue }) {
  const tone = status === "failed" || status === "upload_failed"
    ? "attention"
    : status === "done"
      ? "done"
      : status === "cancelled"
        ? "cancelled"
        : status === "not_checked"
          ? "neutral"
          : "running";
  return (
    <span className={`signal-process-status signal-process-status--${tone}`}>
      <span className="signal-process-status__mark" aria-hidden="true" />
      {PROCESS_LABELS[status]}
    </span>
  );
}
