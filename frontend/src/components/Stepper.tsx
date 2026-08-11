// Stepper — a GOV.UK Task List pattern (design-system.service.gov.uk/
// components/task-list/): one row per stage, a StatusPill immediately
// next to its label (never floated far away), an optional caption line
// below. V-055 rebuild: `done` and `skipped` used to render identically
// (a green checkmark for both), which visually implied more certainty
// than the engine's own "not implemented yet, honestly skipped" state —
// each StepState now gets its own StatusPill tone, reusing the shared,
// colorblind-checked palette (StatusPill.tsx) rather than inventing a
// second status-color system for this one component.
import { StatusPill, type StatusPillTone } from "./StatusPill";
import { cx } from "./cx";

export type StepState = "pending" | "running" | "blocked" | "done" | "skipped" | "attention";

export interface Step {
  id: string;
  label: string;
  state: StepState;
  tagText: string;
  detail?: string;
}

const STEP_TONE: Record<StepState, StatusPillTone> = {
  pending: "neutral",
  running: "info",
  blocked: "attention",
  done: "success",
  skipped: "neutral",
  attention: "attention",
};

// A rail dot's fill: done/skipped/attention read as "this stage settled
// somehow" (filled), pending/running/blocked read as "not settled yet"
// (hollow ring) — a second, non-color signal alongside the StatusPill,
// same WCAG 1.4.1 discipline the rest of this system already applies.
const RAIL_FILLED: Record<StepState, boolean> = {
  pending: false,
  running: false,
  blocked: false,
  done: true,
  skipped: true,
  attention: true,
};

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative flex flex-col rounded-lg border border-border bg-panel">
      {/* Connecting rail (V-056) — Gestalt continuity: five independent
          rows previously read as five unrelated facts, not one pipeline.
          Purely decorative (aria-hidden); the StatusPill text is still
          the only thing screen readers or color-blind users rely on. */}
      <div aria-hidden="true" className="absolute top-6 bottom-6 left-[27px] w-px bg-border" />
      {steps.map((step) => (
        <li
          key={step.id}
          aria-current={step.state === "running" || step.state === "blocked" ? "step" : undefined}
          className="flex gap-3 border-b border-border px-4 py-3 last:border-b-0"
        >
          <span
            aria-hidden="true"
            className="relative z-10 mt-1 h-2.5 w-2.5 flex-none rounded-full"
            style={{
              backgroundColor: RAIL_FILLED[step.state] ? `var(--color-status-${STEP_TONE[step.state]}-text)` : "var(--color-panel)",
              border: `2px solid var(--color-status-${STEP_TONE[step.state]}-text)`,
            }}
          />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <span
                className={cx(
                  "text-sm text-ink",
                  (step.state === "running" || step.state === "blocked") && "font-semibold",
                )}
              >
                {step.label}
              </span>
              <StatusPill tone={STEP_TONE[step.state]}>{step.tagText}</StatusPill>
            </div>
            {step.detail && <p className="text-xs text-ink-secondary">{step.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}
