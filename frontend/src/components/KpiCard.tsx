// KPI card — DESIGN.md §2 (wireframe .pkpi: big number + label)
import type { ReactNode } from "react";

interface KpiCardProps {
  value: ReactNode;
  label: ReactNode;
}

export function KpiCard({ value, label }: KpiCardProps) {
  return (
    <div className="min-w-24 rounded-panel border border-border bg-panel px-3 py-2.5">
      <div className="text-xl font-bold text-ink">{value}</div>
      <div className="text-2xs font-semibold tracking-header uppercase text-table-head">
        {label}
      </div>
    </div>
  );
}
