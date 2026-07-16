// Panel with optional header row — DESIGN.md §2 (wireframe .ppanel / .prow.hd)
import type { ReactNode } from "react";
import { cx } from "./cx";

interface PanelProps {
  header?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function Panel({ header, children, className }: PanelProps) {
  return (
    <div
      className={cx(
        "overflow-hidden rounded-panel border border-border bg-panel",
        className,
      )}
    >
      {header !== undefined && (
        <div className="bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header uppercase text-table-head">
          {header}
        </div>
      )}
      {children}
    </div>
  );
}
