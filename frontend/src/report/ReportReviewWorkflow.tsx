import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import type { EvidenceItem, ReportOut } from "../api/types";
import { useMe } from "../auth/useAuth";
import { Button } from "../ui/Button";
import { SignalDecisionPanel } from "./SignalDecisionPanel";
import { SignalCriteriaResults, SignalEscalatedPanel, SignalFlagsPanel } from "./SignalReviewSections";
import type { CriterionEvidenceSelection } from "./CriterionEvidenceDialog";

const CriterionEvidenceDialog = lazy(async () => ({ default: (await import("./CriterionEvidenceDialog")).CriterionEvidenceDialog }));

const STEPS = [
  { key: "criteria", label: "Review criteria", target: "review-criteria" },
  { key: "findings", label: "Review findings", target: "integrity-signals" },
  { key: "record", label: "Read saved grades", target: "criteria-results-heading" },
  { key: "decision", label: "Make decision", target: "final-decision" },
] as const;
type Step = typeof STEPS[number]["key"];
interface Props {
  report: ReportOut; manuscriptLabel: string;
  onEvidenceSelected?: (criterionId: number, evidence: EvidenceItem, index: number) => void;
  onOpenEvidence?: (criterionId: number, evidence: EvidenceItem, index: number) => void;
  onEditingChange?: (editing: boolean) => void;
}

export function ReportReviewWorkflow(props: Props) {
  const actor = useMe();
  if (actor.isPending) return <p role="status">Checking your instructor session…</p>;
  if (!actor.isSuccess || !actor.data) return <p role="status">Sign in again before reviewing this report.</p>;
  return <WorkflowContent key={`${props.report.check_run_id}:${actor.data?.id ?? "pending"}`} {...props} />;
}

function WorkflowContent({ report, manuscriptLabel, onEvidenceSelected, onOpenEvidence, onEditingChange }: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [source, setSource] = useState<CriterionEvidenceSelection>();
  const changeEditing = useCallback((value: boolean) => { setEditing(value); onEditingChange?.(value); if (!value) setSource(undefined); }, [onEditingChange]);
  useEffect(() => () => onEditingChange?.(false), [onEditingChange]);
  const params = new URLSearchParams(location.search);
  const requested = STEPS.find(step => step.key === params.get("review_step"))
    ?? STEPS.find(step => `#${step.target}` === location.hash) ?? STEPS[0];
  const step = editing ? STEPS[0] : requested;
  const index = STEPS.indexOf(step);
  const heading = useRef<HTMLHeadingElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  useEffect(() => {
    const shouldFocus = mounted.current || Boolean(location.hash);
    mounted.current = true;
    if (shouldFocus) {
      const focusTarget = () => {
        const target = document.getElementById(step.target);
        if (!target) return false;
        target.focus(); target.scrollIntoView({ block: "start" }); return true;
      };
      if (!focusTarget()) {
        heading.current?.focus();
        const observer = new MutationObserver(() => { if (focusTarget()) observer.disconnect(); });
        if (content.current) observer.observe(content.current, { childList: true, subtree: true });
        return () => observer.disconnect();
      }
    }
  }, [step.key, step.target, location.hash]);
  function select(next: Step) {
    if (editing) return;
    const search = new URLSearchParams(location.search);
    search.set("review_step", next);
    navigate({ pathname: location.pathname, search: `?${search}`, hash: `#${STEPS.find(item => item.key === next)!.target}` });
  }
  function openEvidence(criterionId: number, evidence: EvidenceItem, evidenceIndex: number) {
    if (onOpenEvidence) onOpenEvidence(criterionId, evidence, evidenceIndex);
    else setSource({ criterionId, evidence, index: evidenceIndex });
  }
  return <div className="signal-review-workflow">
    <nav className="signal-report-jumps" aria-label="Report review order">{STEPS.map((item, i) => <button key={item.key} type="button" disabled={editing} aria-current={step.key === item.key ? "step" : undefined} onClick={() => select(item.key)}><span>{i + 1}</span>{item.label}</button>)}</nav>
    <h2 ref={heading} tabIndex={-1}>Step {index + 1} of {STEPS.length}: {step.label}</h2>
    <p>{Math.max(0, report.results.length - report.pending_review_count)} of {report.results.length} criteria recorded</p>
    {editing && <p role="status">Save or cancel this grade before changing steps.</p>}
    <div ref={content} className="signal-review-stage">
      {step.key === "criteria" && <><SignalEscalatedPanel checkRunId={report.check_run_id} focused onEditingChange={changeEditing} onEvidenceSelected={onEvidenceSelected} onOpenEvidence={openEvidence} />{report.pending_review_count === 0 && <p>All grades saved. Continue to findings.</p>}</>}
      {step.key === "findings" && <SignalFlagsPanel checkRunId={report.check_run_id} unresolvedHighFlagCount={report.unresolved_high_flag_count} originalityScope={report.originality_scope} report={report} />}
      {step.key === "record" && <SignalCriteriaResults results={report.results.filter(row => row.outcome !== "escalated")} checkRunId={report.check_run_id} onOpenEvidence={openEvidence} />}
      {step.key === "decision" && <SignalDecisionPanel report={report} manuscriptLabel={manuscriptLabel} onReviewCriteria={() => select("criteria")} />}
    </div>
    <div className="signal-control-cluster signal-review-step-actions"><Button variant="secondary" disabled={editing || index === 0} onClick={() => select(STEPS[index - 1].key)}>Back</Button>{index < STEPS.length - 1 && <Button variant="brand" disabled={editing} onClick={() => select(STEPS[index + 1].key)}>Continue to {index === 0 ? "findings" : index === 1 ? "saved grades" : "decision"}</Button>}</div>
    {source && <Suspense fallback={<p role="status">Loading manuscript reader…</p>}><CriterionEvidenceDialog report={report} selection={source} onClose={() => setSource(undefined)} /></Suspense>}
  </div>;
}
