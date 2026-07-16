// Status pill — DESIGN.md §1 status palette (wireframe .ppill.*).
// Readiness statuses always render as pills, never bare text (DESIGN.md §6);
// the label text itself is the non-color signal (DESIGN.md §4).
import type { ReactNode } from "react";
import { cx } from "./cx";

export type PillStatus = "ok" | "warn" | "bad" | "processing" | "queued";

const STATUS_CLASSES: Record<PillStatus, string> = {
  ok: "bg-status-ok-bg text-status-ok-text",
  warn: "bg-status-warn-bg text-status-warn-text",
  bad: "bg-status-bad-bg text-status-bad-text",
  processing: "bg-status-processing-bg text-status-processing-text",
  queued: "bg-status-queued-bg text-status-queued-text",
};

interface PillProps {
  status: PillStatus;
  children: ReactNode;
}

export function Pill({ status, children }: PillProps) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-pill px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        STATUS_CLASSES[status],
      )}
    >
      {children}
    </span>
  );
}
