// New colorblind-checked status pill (V-055, screen 4e) — see tokens.css's
// status-palette comment. Distinct from the legacy `Pill` component (still
// used by not-yet-rebuilt screens with the old ok/warn/bad/processing/
// queued API); this one consolidates once every Pill consumer is rebuilt.
import type { ReactNode } from "react";
import { cx } from "./cx";

export type StatusPillTone = "success" | "info" | "level" | "neutral" | "attention" | "caution" | "danger";

const TONE_CLASSES: Record<StatusPillTone, string> = {
  success: "bg-status-success-bg text-status-success-text",
  info: "bg-status-info-bg text-status-info-text",
  // V-069 (`ux-critic` finding, live-reproduced): a decided levelled-
  // criterion result (e.g. "Proficient") is informational, not a pass/
  // fail valence -- `info`'s own blue token pairing was the right color
  // choice, but `info`'s icon is a genuine loading spinner (every OTHER
  // `info` usage, ApiStatus.tsx/manuscriptStatus.ts, really is "still in
  // progress" -- confirmed live via getComputedStyle, it is actually
  // spinning). Reusing it for a FINISHED, human-legible verdict told the
  // instructor something was still processing. Same color tokens as
  // `info`, a static ascending-bars icon instead (a "rung on a scale"
  // metaphor, distinct from every other tone's icon).
  level: "bg-status-info-bg text-status-info-text",
  neutral: "bg-status-neutral-bg text-status-neutral-text",
  // System/process state only (ingestion failure, quota, a degraded stage)
  // — never a readiness verdict, see tokens.css (V-056).
  attention: "bg-status-attention-bg text-status-attention-text",
  // The ambiguous middle case a human must judge (Conditionally Ready) —
  // new in V-056, replaces attention's amber for readiness verdicts.
  caution: "bg-status-caution-bg text-status-caution-text",
  // The worst readiness verdict (Not Ready) — new in V-056, reads at the
  // same weight as the system's other red-alert language.
  danger: "bg-status-danger-bg text-status-danger-text",
};

function ToneIcon({ tone }: { tone: StatusPillTone }) {
  const common = {
    "aria-hidden": true as const,
    viewBox: "0 0 24 24",
    width: 14,
    height: 14,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (tone) {
    case "success":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M8 12.5l2.5 2.5L16 9" />
        </svg>
      );
    case "info":
      return (
        <svg {...common} className="motion-safe:animate-spin motion-reduce:animate-none">
          <path d="M20 12a8 8 0 1 0-2.5 5.8" />
          <path d="M20 8v4h-4" />
        </svg>
      );
    case "level":
      return (
        <svg {...common}>
          <line x1="6" y1="16" x2="6" y2="19" />
          <line x1="12" y1="12" x2="12" y2="19" />
          <line x1="18" y1="8" x2="18" y2="19" />
        </svg>
      );
    case "neutral":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <line x1="8" y1="12" x2="16" y2="12" />
        </svg>
      );
    case "attention":
      return (
        <svg {...common}>
          <path d="M12 3.5 21 19.5H3Z" />
          <line x1="12" y1="10" x2="12" y2="14" />
          <circle cx="12" cy="17" r="0.5" fill="currentColor" />
        </svg>
      );
    case "caution":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="7.5" x2="12" y2="13" />
          <circle cx="12" cy="16.5" r="0.5" fill="currentColor" />
        </svg>
      );
    case "danger":
      return (
        <svg {...common}>
          <path d="M12 2 21.5 7v10L12 22 2.5 17V7Z" />
          <line x1="12" y1="8" x2="12" y2="13" />
          <circle cx="12" cy="16" r="0.5" fill="currentColor" />
        </svg>
      );
  }
}

export function StatusPill({ tone, children }: { tone: StatusPillTone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap",
        TONE_CLASSES[tone],
      )}
    >
      <ToneIcon tone={tone} />
      {children}
    </span>
  );
}
