// Severity tag — DESIGN.md §1 ("tags reuse pill palettes"; wireframe .ptag.*)
import type { ReactNode } from "react";
import { cx } from "./cx";

export type Severity = "high" | "medium" | "low";

const SEVERITY_CLASSES: Record<Severity, string> = {
  high: "bg-status-bad-bg text-status-bad-text",
  medium: "bg-status-warn-bg text-status-warn-text",
  low: "bg-status-queued-bg text-status-queued-text",
};

interface TagProps {
  severity: Severity;
  children: ReactNode;
}

export function Tag({ severity, children }: TagProps) {
  return (
    <span
      className={cx(
        "inline-flex rounded-tag px-1.5 py-px text-2xs font-semibold whitespace-nowrap",
        SEVERITY_CLASSES[severity],
      )}
    >
      {children}
    </span>
  );
}
