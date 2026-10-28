// F8, screens 4h/4w: keep a recorded assessment with its content-bound source.
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { instructorAnchor } from "../domain/sourceAnchor";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import type { EvidenceReference, EvidenceSourceRequest } from "./evidenceTypes";

const RecordedEvidenceReader = lazy(() => import("../document/RecordedEvidenceReader").then(module => ({ default: module.RecordedEvidenceReader })));

export interface CriterionEvidenceContext {
  title: string;
  explanation: string | null;
  explanationLabel: string;
  recordedNote: string | null;
  references: readonly EvidenceReference[];
}

export function CriterionEvidenceDialog({ run, request, context, onClose }: {
  run: number; request: EvidenceSourceRequest; context: CriterionEvidenceContext; onClose: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [manual, setManual] = useState(false);
  const placement = useRef<FocusContinuation | undefined>(undefined);
  useEffect(() => {
    placement.current = claimFocusContinuation();
    return () => placement.current?.dispose();
  }, []);
  const retry = (action: HTMLElement) => {
    placement.current?.dispose();
    placement.current = claimFocusContinuation(action);
  };
  return <Dialog title={`Evidence and explanation: ${context.title}`} wide onClose={onClose}
    actions={<><Button variant="quiet" onClick={event => {
      retry(event.currentTarget); setManual(false); setAttempt(value => value + 1);
    }}>Reload manuscript</Button><Button variant="secondary" onClick={onClose}>Close manuscript</Button></>}>
    <div className="signal-criterion-source-dialog">
      <h3>{context.title}</h3>
      <section className="signal-resolution-context-note" aria-label="Recorded assessment">
        <h4>Assessment and explanation</h4>
        {context.explanation?.trim() ? <><strong>{context.explanationLabel}</strong><p>{context.explanation}</p></>
          : <p>No assessment explanation was recorded for this criterion.</p>}
        {context.recordedNote?.trim() && <p><strong>Recorded system note:</strong> {context.recordedNote}</p>}
      </section>
      <section className="signal-criterion-packet" aria-label="Saved evidence">
        <h4>Saved evidence</h4>
        <p className="signal-field-hint">All passages saved for this criterion. No finer evidence grouping was recorded.</p>
        <div className="signal-criterion-packet__quotations">{context.references.map(reference => <figure key={reference.reference_id}
          data-selected={reference.reference_id === request.reference.reference_id || undefined}>
          <figcaption><strong>Saved passage {reference.index + 1}</strong><span>{instructorAnchor(reference.anchor ?? "")}</span></figcaption>
          <blockquote>“{reference.quote}”</blockquote>
        </figure>)}</div>
      </section>
      <p className="signal-field-hint">Your grade and comment stay in the review form. Close this manuscript to continue grading.</p>
      <div className="signal-criterion-source-reader"><Suspense fallback={<p role="status">Loading the source reader.</p>}><RecordedEvidenceReader key={attempt} run={run} request={request}
        current={!manual} visible attempt={attempt} canPlace={() => placement.current?.owns() ?? false}
        onRetry={retry} onManual={() => { placement.current?.dispose(); setManual(true); }} /></Suspense></div>
    </div>
  </Dialog>;
}
