// D-023 (BUG-051/052/098): a criterion's weight is a RELATIVE value (no
// required total, `report/scoring.py` normalises it) -- rendered as the
// same Low/Medium/High severity-style scale `SeverityTag.tsx` uses for
// flags, reusing its exact color tokens, rather than a bare percentage
// that asserted a scale the value doesn't have. A separate component
// (not a reskin of SeverityTag) because the label reads "importance",
// never "severity" -- a heavy criterion isn't a risk finding.
import { cx } from "./cx";

export type WeightImportance = "low" | "med" | "high";

const IMPORTANCE_META: Record<WeightImportance, { label: string; bg: string; text: string }> = {
  high: { label: "High", bg: "bg-severity-high-bg", text: "text-severity-high-text" },
  med: { label: "Medium", bg: "bg-severity-med-bg", text: "text-severity-med-text" },
  low: { label: "Low", bg: "bg-severity-low-bg", text: "text-severity-low-text" },
};

export function WeightImportanceTag({ importance }: { importance: WeightImportance }) {
  const m = IMPORTANCE_META[importance];
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap",
        m.bg,
        m.text,
      )}
      title={`${m.label} importance`}
    >
      {m.label}
    </span>
  );
}
