// F5/F8, screen 4i: an explicit account-scoped source-existence judgment.
import { useId, useRef, useState } from "react";
import type { FlagOut } from "../api/types";
import { INSTRUCTOR_NOTE_MAX_LENGTH } from "../config/ui";
import { isAssessmentRevision } from "../report/assessmentRevision";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { FlagActionCurrentReview } from "./FlagActionCurrentReview";
import { useConfirmCitationSource } from "./useFlag";
import { useFlagActionReview } from "./useFlagActionReview";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { useFindingCompletion } from "./useFindingCompletion";
import { fieldRefusal, formRefusal, isValidationRefusal, isServiceBusy, REFUSAL_NOTHING_CHANGED, REFUSAL_TITLE, SERVICE_BUSY_COPY, useRefusalFocus } from "../api/fieldRefusal";
import { RefusalAlert } from "../ui/RefusalAlert";

export function ConfirmCitationSourceModal({ flag, onClose, onAccepted, continuationIsCurrent, fallbackFocusRef }: { flag: FlagOut; onClose: () => void; onAccepted: (flag: FlagOut) => boolean; continuationIsCurrent: () => boolean; fallbackFocusRef?: React.RefObject<HTMLHeadingElement | null> }) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const confirm = useConfirmCitationSource(flag.id);
  const review = useFlagActionReview(flag, "confirm-source");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const followAction = useExplicitActionFocus(() => reasonRef.current);
  const errorId = useId();
  const refusalId = useId();
  const refusal = fieldRefusal(confirm.error, "reason");
  const requestRefusal = formRefusal(confirm.error, ["reason"]);
  useRefusalFocus(refusal, reasonRef);
  const invalid = attempted && !reason.trim();
  const completion = useFindingCompletion();
  const [accepted, setAccepted] = useState(false);
  function submit() {
    if (accepted || !review.isCurrent() || !review.canSubmit || !isAssessmentRevision(review.expectedRevision)) return;
    setAttempted(true);
    if (!reason.trim()) { reasonRef.current?.focus(); return; }
    completion.begin(reasonRef.current);
    confirm.mutate({ reason: reason.trim(), expected_revision: review.expectedRevision }, {
      onSuccess: out => {
        if (!review.isCurrent()) return;
        if (onAccepted(out)) { completion.accept(); setAccepted(true); }
        else review.freeze();
      // A refusal of the reason is definite and changes nothing; the review stays open.
      }, onError: (error) => { if (!isValidationRefusal(error) && !isServiceBusy(error)) review.freeze(); },
    });
  }
  return <Dialog title="Confirm this source exists?" initialFocusTitle fallbackFocusRef={fallbackFocusRef}
    returnFocusTarget={() => completion.returnTarget(fallbackFocusRef?.current ?? null)} shouldRestoreFocus={() => completion.shouldRestore(continuationIsCurrent)}
    onClose={confirm.isPending || accepted ? undefined : onClose}
    actions={<><Button variant="secondary" disabled={confirm.isPending || accepted} onClick={onClose}>Cancel</Button>
      <Button variant="brand" disabled={!review.canSubmit || accepted} busy={confirm.isPending} onClick={submit}>Confirm this source</Button></>}>
    <div className="signal-group-flow" aria-busy={confirm.isPending}>
      <h3>Reference being reviewed</h3>
      <blockquote className="max-h-32 overflow-y-auto rounded-md border border-border bg-page px-3 py-2 text-sm break-words text-ink">{flag.evidence_excerpt}</blockquote>
      {flag.citation_source_key && <p>Source key: {flag.citation_source_key.kind} - {flag.citation_source_key.value}</p>}
      {flag.citation_source_key?.kind === "title" && <Alert title="Title matches are less precise" tone="warning">Check that this is the reference cited in the manuscript.</Alert>}
      <p>This resolves this finding and recalculates this report. VERIDICAL may reuse your confirmation for this exact reference in later checks in your account.</p>
      <p>It does not confirm other references or change another instructor's reports. Confirming source existence does not establish support for a manuscript claim.</p>
      <p>This action cannot be undone through the interface.</p>
      <FlagActionCurrentReview review={review} busy={confirm.isPending} onRebind={trigger => followAction(trigger, review.rebind)} />
      <label className="signal-evidence-field"><span>Where you verified this (required)</span>
        <textarea ref={reasonRef} rows={4} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH} value={reason}
          disabled={confirm.isPending} aria-invalid={invalid || Boolean(refusal) || undefined} aria-describedby={invalid ? errorId : refusal ? refusalId : undefined}
          onChange={event => { if (refusal) confirm.reset(); setReason(event.target.value); review.reasonChanged(); setAttempted(false); }} />
      </label>
      {invalid && <p id={errorId} role="alert" className="signal-field-error">Enter where you verified this before confirming.</p>}
      {!invalid && refusal && <p id={refusalId} className="signal-field-error">{refusal} {REFUSAL_NOTHING_CHANGED}</p>}
      {requestRefusal && <RefusalAlert title={REFUSAL_TITLE} message={requestRefusal} takeFocus={!refusal} />}
      {isServiceBusy(confirm.error) && <Alert title={SERVICE_BUSY_COPY.title} tone="error" role="alert">{SERVICE_BUSY_COPY.body}</Alert>}
        {confirm.isError && !isValidationRefusal(confirm.error) && !isServiceBusy(confirm.error) && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current assessment before trying again.</Alert>}
    </div>
  </Dialog>;
}
