// Chip — small pill for page-identifying metadata (e.g. a rubric title,
// a count). Optional truncation for long values (title tooltip carries
// the full string, same pattern already shipped on ManuscriptsTable.tsx).
import type { ReactNode } from "react";
import { cx } from "./cx";

interface ChipProps {
  children: ReactNode;
  title?: string;
  maxWidthClass?: string;
}

export function Chip({ children, title, maxWidthClass }: ChipProps) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-sm whitespace-nowrap text-ink-secondary",
      )}
    >
      <span className={cx(maxWidthClass, maxWidthClass && "truncate")}>{children}</span>
    </span>
  );
}
