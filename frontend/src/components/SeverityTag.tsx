// Severity indicator (screen 4i) — a fixed 3-level ordinal risk scale,
// deliberately a separate component from StatusPill: StatusPill encodes
// a process/verdict state (pass/fail/pending) with an icon that means
// "what kind of outcome"; this encodes "how much" via a rising 3-bar
// level meter, a different icon grammar entirely. A live deuteranopia/
// protanopia simulation (V-055, tokens.css's severity-ramp comment)
// showed the high/medium colors converging under both conditions, so
// the bar-count icon (not just color) is what actually keeps the three
// levels distinguishable.
import { cx } from "./cx";

export type Severity = "high" | "med" | "low";

const SEVERITY_META: Record<Severity, { label: string; bg: string; text: string; bars: number }> = {
  high: { label: "High", bg: "bg-severity-high-bg", text: "text-severity-high-text", bars: 3 },
  med: { label: "Medium", bg: "bg-severity-med-bg", text: "text-severity-med-text", bars: 2 },
  low: { label: "Low", bg: "bg-severity-low-bg", text: "text-severity-low-text", bars: 1 },
};

function LevelIcon({ bars }: { bars: number }) {
  const heights = [8, 14, 20];
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14">
      {heights.map((h, i) => (
        <rect
          key={i}
          x={3 + i * 7}
          y={20 - h}
          width="4"
          height={h}
          rx="1"
          fill={i < bars ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth={i < bars ? 0 : 1.5}
          opacity={i < bars ? 1 : 0.35}
        />
      ))}
    </svg>
  );
}

export function SeverityTag({ severity }: { severity: Severity }) {
  const m = SEVERITY_META[severity];
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap",
        m.bg,
        m.text,
      )}
    >
      <LevelIcon bars={m.bars} />
      {m.label} severity
    </span>
  );
}
