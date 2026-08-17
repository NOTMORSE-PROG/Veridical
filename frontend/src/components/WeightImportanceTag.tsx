// D-023 (BUG-051/052/098): a criterion's weight is a RELATIVE value (no
// required total, `report/scoring.py` normalises it) -- rendered as a
// Low/Medium/High tag instead of a bare percentage that asserted a scale
// the value doesn't have.
//
// `backend-critic` finding (live-reproduced): the first version of this
// component reused `SeverityTag.tsx`'s exact color tokens (severity-high
// -> danger red, severity-med -> caution amber) -- but a heavily-weighted
// criterion is not a risk finding, and a report page can show both this
// tag and real severity tags side by side. Reusing danger red for "High
// importance" reintroduces the EXACT defect D-023 exists to remove
// (asserting an alarm the value doesn't have), just visually instead of
// numerically. Fixed by using the neutral text-intensity scale
// (ink/ink-secondary/ink-tertiary) this app already uses for typographic
// hierarchy everywhere else, on the same neutral background every tier --
// a magnitude cue, never a valence cue. No new tokens invented (rule 7);
// `export.py`'s PDF equivalent mirrors this with its own existing
// _INK/_INK_SECONDARY/_INK_TERTIARY constants.
import { cx } from "./cx";

export type WeightImportance = "low" | "med" | "high";

const IMPORTANCE_META: Record<WeightImportance, { label: string; text: string; weight: string }> = {
  high: { label: "High", text: "text-ink", weight: "font-bold" },
  med: { label: "Medium", text: "text-ink-secondary", weight: "font-semibold" },
  low: { label: "Low", text: "text-ink-tertiary", weight: "font-medium" },
};

export function WeightImportanceTag({ importance }: { importance: WeightImportance }) {
  const m = IMPORTANCE_META[importance];
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full bg-status-neutral-bg px-2 py-0.5 text-xs whitespace-nowrap",
        m.text,
        m.weight,
      )}
      title={`${m.label} importance`}
    >
      {m.label}
    </span>
  );
}
