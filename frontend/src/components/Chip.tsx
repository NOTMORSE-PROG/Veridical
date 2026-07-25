// Chip — DESIGN.md §2 (wireframe .pchip)
import type { ReactNode } from "react";

interface ChipProps {
  children: ReactNode;
}

export function Chip({ children }: ChipProps) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-pill border border-modal-border bg-panel px-2.5 py-0.5 text-xs whitespace-nowrap text-ink-soft">
      {children}
    </span>
  );
}
