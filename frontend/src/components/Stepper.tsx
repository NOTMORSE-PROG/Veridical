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

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col rounded-lg border border-border bg-panel">
      {steps.map((step) => (
        <li
          key={step.id}
          aria-current={step.state === "running" || step.state === "blocked" ? "step" : undefined}
          className="flex flex-col gap-1 border-b border-border px-4 py-3 last:border-b-0"
        >
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
        </li>
      ))}
    </ol>
  );
}
