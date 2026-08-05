// New colorblind-checked status pill (V-055, screen 4e) — see tokens.css's
// status-palette comment. Distinct from the legacy `Pill` component (still
// used by not-yet-rebuilt screens with the old ok/warn/bad/processing/
// queued API); this one consolidates once every Pill consumer is rebuilt.
import type { ReactNode } from "react";
import { cx } from "./cx";

export type StatusPillTone = "success" | "info" | "neutral" | "attention";

const TONE_CLASSES: Record<StatusPillTone, string> = {
  success: "bg-status-success-bg text-status-success-text",
  info: "bg-status-info-bg text-status-info-text",
  neutral: "bg-status-neutral-bg text-status-neutral-text",
  attention: "bg-status-attention-bg text-status-attention-text",
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
