import type { ReportOut } from "../api/types";
import { DECISION_LABEL } from "../domain/decisionTone";
import { REPORT_STEPS, type ReportStep } from "../report/reviewStep";
import { Button } from "../ui/Button";

export interface WorkspaceStepCounts {
  pendingCriteria: number | undefined;
  openFindings: number | undefined;
  recordedGrades: number | undefined;
  decision: ReportOut["decision"] | undefined;
}

// Counts are plain quantities (how many are left), not judgments, so they
// stay numbers; the decision step shows the recorded decision's own label.
function stepStatus(step: ReportStep, counts: WorkspaceStepCounts): string {
  if (step === "criteria") return counts.pendingCriteria === undefined ? "Loading" : counts.pendingCriteria ? `${counts.pendingCriteria} left` : "None left";
  if (step === "findings") return counts.openFindings === undefined ? "Loading" : counts.openFindings ? `${counts.openFindings} open` : "None open";
  if (step === "grades") return counts.recordedGrades === undefined ? "Loading" : `${counts.recordedGrades} recorded`;
  return counts.decision ? DECISION_LABEL[counts.decision] : "Not recorded";
}

export function WorkspaceStepNavigation({ current, counts, onSelect }: { current: ReportStep | null; counts: WorkspaceStepCounts; onSelect: (step: ReportStep) => void }) {
  return <nav className="signal-workspace-steps" aria-label="Review steps">
    <ol>{REPORT_STEPS.map((step, index) => {
      const status = stepStatus(step.id, counts);
      return <li key={step.id}>
        <button type="button" id={`workspace-step-${step.id}`} aria-current={current === step.id ? "step" : undefined}
          aria-label={`Step ${index + 1}: ${step.label}. ${status}`} onClick={() => onSelect(step.id)}>
          <span className="signal-workspace-steps__number" aria-hidden="true">{index + 1}</span>
          <span className="signal-workspace-steps__label">{step.label}</span>
          <span className="signal-workspace-steps__status">{status}</span>
        </button>
      </li>;
    })}</ol>
  </nav>;
}

export function WorkspaceStepHeading({ step }: { step: ReportStep }) {
  const index = REPORT_STEPS.findIndex(item => item.id === step);
  return <p className="signal-section-kicker signal-workspace-step-kicker">Step {index + 1} of {REPORT_STEPS.length}</p>;
}

export function WorkspaceStepFooter({ current, onSelect }: { current: ReportStep; onSelect: (step: ReportStep) => void }) {
  const index = REPORT_STEPS.findIndex(item => item.id === current);
  const previous = index > 0 ? REPORT_STEPS[index - 1] : null;
  const next = index < REPORT_STEPS.length - 1 ? REPORT_STEPS[index + 1] : null;
  return <nav className="signal-workspace-step-footer" aria-label="Move between review steps">
    {previous && <Button variant="quiet" onClick={() => onSelect(previous.id)}>Back: {previous.label}</Button>}
    {next && <Button variant="primary" onClick={() => onSelect(next.id)}>Continue: {next.label}</Button>}
  </nav>;
}
