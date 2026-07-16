// Stepper — DESIGN.md §1 stepper palette (wireframe .pstep / .pstep.done /
// .pstep.run). Each state has a distinct glyph so state is never conveyed by
// color alone (DESIGN.md §4).
import { cx } from "./cx";

export type StepState = "done" | "running" | "pending";

export interface Step {
  label: string;
  state: StepState;
  note?: string;
}

const STEP_CIRCLE_CLASSES: Record<StepState, string> = {
  done: "border-step-done bg-step-done text-on-primary",
  running: "border-primary bg-primary text-on-primary",
  pending: "border-step-pending-border bg-panel text-step-pending-text",
};

const STEP_GLYPHS: Record<StepState, string> = {
  done: "✓",
  running: "●",
  pending: "○",
};

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((step, index) => (
        // Index keys are safe: the list is static per render, never reordered
        <li key={index} className="flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className={cx(
              "flex size-5 flex-none items-center justify-center rounded-pill border-2 text-2xs font-bold",
              STEP_CIRCLE_CLASSES[step.state],
            )}
          >
            {STEP_GLYPHS[step.state]}
          </span>
          <span className={cx("flex-1", step.state === "running" && "font-semibold")}>
            {step.label}
          </span>
          {step.note !== undefined && (
            <span className="text-xs text-ink-faint">{step.note}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
