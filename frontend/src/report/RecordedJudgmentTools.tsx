import { AUTOMATIC_GRADE_COPY } from "../domain/automaticGrade";
import { ActionAssessmentContext } from "./ReportAssessmentContext";
// F3.5/F8.3, screens 4h/4w: revise a saved criterion in its evidence context.
import { useLayoutEffect, useRef, useState } from "react";
import type { JudgmentSnapshot } from "../api/judgments";
import { INSTRUCTOR_NOTE_MAX_LENGTH } from "../config/ui";
import { CRITERION_EXCLUSION_COPY, recordedInstructorComment } from "../domain/criterionReview";
import { useFindingCompletion } from "../flags/useFindingCompletion";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useCriterionReviewScope } from "./criterionReviewContext";
import { criterionRecordId } from "./savedJudgment";
import { judgmentLabel, judgmentVersion, originalJudgmentLabel, sameJudgmentGrade } from "./judgmentRecord";
import type { RevisionDraft } from "./useJudgmentRevisions";
import type { JudgmentRevisionOutput } from "../api/judgments";
import type { ResultRowCommon } from "../api/types";

function Snapshot({ value, attributionOnly = false }: { value: JudgmentSnapshot; attributionOnly?: boolean }) {
  return <div className="signal-attached-flow">
    {!attributionOnly && <><p><strong>{judgmentLabel(value)}</strong></p>
      {value.resolution_type === "needs_document" && <p>{CRITERION_EXCLUSION_COPY.noGrade}</p>}
      <p><strong>Instructor reason:</strong> {recordedInstructorComment(value.resolution_type, value.reason) ?? "No instructor reason is available in this record."}</p></>}
    <p className="signal-field-hint">{value.actor_id === null ? "Instructor attribution unavailable." : `Recorded by instructor #${value.actor_id}.`}
      {value.recorded_at === null ? " Original save time unavailable." : <> <time dateTime={value.recorded_at}>{new Date(value.recorded_at).toLocaleString()}</time></>}</p>
    {value.provenance === "legacy_partial" && <p className="signal-field-hint">Earlier saved judgment. Some original record details are unavailable.</p>}
  </div>;
}

export function RecordedJudgmentTools({ checkRunId, criterionId, criterionText, canonical }: {
  checkRunId: number; criterionId: number; criterionText: string; canonical: ResultRowCommon;
}) {
  const review = useCriterionReviewScope(checkRunId);
  const scope = review.revisions;
  const record = scope.records.get(criterionId);
  const draft = record?.draft;
  const [purpose, setPurpose] = useState<"record" | "revise" | "history" | "current">("current");
  const [confirmation, setConfirmation] = useState<RevisionDraft>();
  const [discarding, setDiscarding] = useState(false);
  const [stopping, setStopping] = useState<RevisionDraft>();
  const [stopError, setStopError] = useState<string>();
  const [sending, setSending] = useState(false);
  const [focusedReceipt, setFocusedReceipt] = useState<JudgmentRevisionOutput>();
  const currentHeading = useRef<HTMLHeadingElement>(null);
  const historyHeading = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const reviseRef = useRef<HTMLButtonElement>(null);
  const reviewRef = useRef<HTMLButtonElement>(null);
  const receiptRef = useRef<HTMLHeadingElement>(null);
  const intent = useRef<{ claim: FocusContinuation; purpose: "record" | "revise" | "history" | "current" } | undefined>(undefined);
  const followRebind = useExplicitActionFocus(() => reasonRef.current ?? currentHeading.current);
  const followDiscard = useExplicitActionFocus(() => reviseRef.current ?? document.getElementById(criterionRecordId(checkRunId, criterionId)));
  const completion = useFindingCompletion();
  useLayoutEffect(() => {
    const pending = intent.current;
    if (!pending) return;
    const target = pending.purpose === "history" ? historyHeading.current : currentHeading.current;
    if (target) { pending.claim.focus(target); intent.current = undefined; }
  });
  useLayoutEffect(() => () => intent.current?.claim.dispose(), []);
  function open(next: "record" | "revise" | "history" | "current", trigger: HTMLElement) {
    intent.current?.claim.dispose();
    intent.current = { claim: claimFocusContinuation(trigger), purpose: next };
    setPurpose(next); scope.open(criterionId, next);
  }
  const id = `judgment-revision-${checkRunId}-${criterionId}`;
  const contextDescription = review.assessmentNotices?.length ? `${id}-context` : undefined;
  const editorContextDescription = contextDescription ? `${id}-editor-context` : undefined;
  const confirmContextDescription = contextDescription ? `${id}-confirm-context` : undefined;
  const reviewContextDescription = contextDescription ? `${id}-review-context` : undefined;
  const writable = draft && scope.canWrite(draft);
  const current = record?.context?.current;
  const recording = draft ? draft.kind === "record" : !canonical.resolution;
  const actionTitle = recording ? "Record your judgment" : "Revise recorded judgment";
  const receipt = focusedReceipt ?? record?.receipt;
  const matchesCanonical = current && current.reason === canonical.resolution?.reason
    && current.outcome === canonical.outcome && current.score === canonical.score
    && current.level?.ordinal === canonical.level?.ordinal && current.level?.name === canonical.level?.name;
  const staleCurrent = record?.context && (record.loading || record.readError || !scope.currentContext(record.context));
  function cancel(trigger: HTMLElement) {
    if (!draft || draft.saving || draft.uncertainRevision !== undefined) return;
    const proposed = scope.proposal(draft);
    if (draft.reason.length || (draft.kind === "record" && draft.resolution !== undefined)
      || (proposed && draft.bound.current && !sameJudgmentGrade(proposed, draft.bound.current))) setDiscarding(true);
    else followDiscard(trigger, () => scope.discard(criterionId));
  }
  const proposal = confirmation && scope.proposal(confirmation);
  return <div className="signal-judgment-tools signal-group-flow">
    {!draft && <ActionAssessmentContext id={`${id}-context`} notices={review.assessmentNotices ?? []} />}
    <div className="signal-control-cluster">
      {!draft && (review.decision
        ? <p>{recording ? "Reopen this report to record an instructor judgment." : "Reopen this report to revise recorded judgments."}</p>
        : <Button ref={reviseRef} variant="secondary" disabled={record?.loading}
          aria-label={`${recording ? AUTOMATIC_GRADE_COPY.change : AUTOMATIC_GRADE_COPY.changeAgain}: ${criterionText}`} aria-describedby={contextDescription}
          onClick={event => open(recording ? "record" : "revise", event.currentTarget)}>{recording ? AUTOMATIC_GRADE_COPY.change : AUTOMATIC_GRADE_COPY.changeAgain}</Button>)}
      {(canonical.resolution || current) && draft?.uncertainRevision === undefined && !record?.abandonedAttempt && <Button variant="quiet" aria-expanded={record?.historyOpen ?? false} aria-controls={`${id}-history`}
        aria-label={`Judgment history: ${criterionText}`} onClick={event => {
          if (record?.historyOpen) scope.closeHistory(criterionId);
          else open("history", event.currentTarget);
        }}>Judgment history</Button>}
    </div>
    {record && <section className="signal-group-flow" aria-labelledby={`${id}-heading`}>
      <h4 id={`${id}-heading`} ref={currentHeading} tabIndex={-1}>{purpose === "record" || purpose === "revise" || draft ? actionTitle : "Current instructor judgment"}</h4>
      {record.loading && purpose !== "history" && <p role="status">{recording ? "Loading the current assessment." : "Loading the current judgment."}</p>}
      {record.readError && <div role="alert"><p>{record.readError}</p>
        {draft && <p>Your draft is kept.</p>}
        <Button variant="secondary" onClick={event => open(purpose === "record" ? "record" : purpose === "history" ? "history" : "current", event.currentTarget)}>
          {purpose === "history" ? "Retry judgment history" : "Retry current judgment"}</Button></div>}
      {current && <div className="signal-attached-flow">
        <h5>{matchesCanonical ? "Recorded attribution" : staleCurrent ? "Last loaded instructor judgment" : "Current instructor judgment"}</h5>
        <Snapshot value={current} attributionOnly={Boolean(matchesCanonical)} />
      </div>}
      {record.context && !record.context.can_revise && !record.context.can_record && <p>{record.context.unavailable_reason === "report_decided"
        ? "Reopen this report to revise recorded judgments."
        : record.context.unavailable_reason === "scale_unavailable" ? "The saved criterion scale could not be read. A revision is unavailable."
          : recording ? "This criterion is not available for an initial instructor judgment. Review the current assessment." : "No recorded instructor judgment is available to revise."}</p>}
      {record.abandonedAttempt && draft?.uncertainRevision === undefined && <div className="signal-attached-flow" role="status">
        <p>{record.abandonedAttempt.kind === "record" ? "An earlier initial judgment save is still unconfirmed. Review the current assessment before making another change." : draft ? "The earlier revision save is still unconfirmed." : "Editing stopped. The earlier revision save is still unconfirmed. Review the current judgment before making another change."}</p>
        <div className="signal-control-cluster"><Button variant="secondary" disabled={record.loading} onClick={event => open("current", event.currentTarget)}>Review current judgment</Button>
          {current && <Button variant="quiet" onClick={event => open("history", event.currentTarget)}>Judgment history</Button>}</div>
      </div>}
      {draft && <div className="signal-group-flow">
        <p>{recording ? "Review the automated assessment and manuscript evidence before recording your judgment." : "Review the recorded assessment and manuscript evidence below before choosing your revision."}</p>
        {recording && <p><strong>Automated assessment:</strong> {originalJudgmentLabel(draft.bound.original_assessment)}. The original assessment remains available after you save.</p>}
        {!writable && !draft.saving && <div className="signal-attached-flow" role="status">
          {draft.uncertainRevision !== undefined ? <><h5>{recording ? "Judgment save is unconfirmed" : "Revision save is unconfirmed"}</h5>
            <p>{recording ? "We could not confirm whether your judgment was saved. Your entry is kept. Review the current assessment before starting another action." : "We could not confirm whether the revision was saved. Your entered judgment and reason are kept in this review. Review the current judgment and its history, or stop editing this revision."}</p></>
            : <p>Your entered revision is kept. Review the current judgment before using this draft.</p>}
          <Button variant="secondary" disabled={record.loading} onClick={event => open("current", event.currentTarget)}>Review current judgment</Button>
          {draft.uncertainRevision !== undefined && <>
            {current && <Button variant="quiet" onClick={event => open("history", event.currentTarget)}>Judgment history</Button>}
            <Button variant="secondary" disabled={!scope.canStop()} onClick={() => { setStopError(undefined); setStopping(draft); }}>{recording ? "Stop editing this judgment" : "Stop editing this revision"}</Button>
          </>}
          {scope.canRebind(criterionId) && <Button variant="secondary" onClick={event => followRebind(event.currentTarget, () => scope.rebind(criterionId))}>{recording && current ? "Use this draft for a revision" : recording ? "Use this draft with the current assessment" : "Use this draft with the current judgment"}</Button>}
        </div>}
        <ActionAssessmentContext id={`${id}-editor-context`} notices={review.assessmentNotices ?? []} />
        <fieldset disabled={!writable} className="signal-group-flow" aria-describedby={editorContextDescription}><legend>{recording ? "Your judgment" : "Revised judgment"}</legend>
          {draft.bound.choices.kind === "levelled" ? draft.bound.choices.levels.map(level => <label key={level.level} className="signal-choice">
            <input type="radio" name={`${id}-choice`} checked={draft.resolution === "mark_level" && draft.level === level.level}
              onChange={() => scope.change(criterionId, { resolution: "mark_level", level: level.level })} />
            <span><strong>{level.name}</strong>{level.descriptor && <span className="signal-field-hint"> {level.descriptor}</span>}</span>
          </label>) : [ ["mark_pass", "Meets criterion"], ["mark_fail", "Does not meet"] ].map(([resolution, label]) => <label key={resolution} className="signal-choice">
            <input type="radio" name={`${id}-choice`} checked={draft.resolution === resolution}
              onChange={() => scope.change(criterionId, { resolution: resolution as "mark_pass" | "mark_fail", level: undefined })} /><span>{label}</span>
          </label>)}
          {draft.bound.choices.exclusion_allowed && <label className="signal-choice"><input type="radio" name={`${id}-choice`}
            checked={draft.resolution === "needs_document"} onChange={() => scope.change(criterionId, { resolution: "needs_document", level: undefined })} />
            <span><strong>{CRITERION_EXCLUSION_COPY.action}</strong><span className="signal-field-hint"> {CRITERION_EXCLUSION_COPY.noGrade}</span></span></label>}
        </fieldset>
        <div className="signal-field signal-evidence-field"><label htmlFor={`${id}-reason`}>{recording ? "Reason for your judgment (required)" : "New reason for revision (required)"}</label>
          <p id={`${id}-hint`} className="signal-field-hint">{recording ? "Explain your source-based judgment. The automated assessment is retained separately from your judgment." : "Explain the judgment you are recording now. The previous judgment and reason will remain in your private judgment history."}</p>
          <textarea id={`${id}-reason`} ref={reasonRef} value={draft.reason} required disabled={!writable} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH}
            aria-describedby={`${editorContextDescription ? `${editorContextDescription} ` : ""}${id}-hint${draft.error && draft.uncertainRevision === undefined ? ` ${id}-error` : ""}`}
            aria-invalid={draft.attempted && Boolean(draft.error) && draft.uncertainRevision === undefined}
            onChange={event => scope.change(criterionId, { reason: event.target.value })} />
        </div>
        {draft.error && draft.uncertainRevision === undefined && <p id={`${id}-error`} role="alert">{draft.error}</p>}
        <ActionAssessmentContext id={`${id}-review-context`} notices={review.assessmentNotices ?? []} />
        <div className="signal-control-cluster"><Button ref={reviewRef} aria-describedby={reviewContextDescription} variant="brand" disabled={!writable} busy={draft.saving}
          onClick={() => { if (scope.review(criterionId)) setConfirmation({ ...draft }); else reasonRef.current?.focus(); }}>{recording ? "Review judgment" : "Review revision"}</Button>
          <Button variant="secondary" disabled={draft.saving || draft.uncertainRevision !== undefined} onClick={event => cancel(event.currentTarget)}>{recording ? "Cancel judgment" : "Cancel revision"}</Button></div>
      </div>}
      {receipt && <div className="signal-attached-flow" onFocusCapture={() => setFocusedReceipt(receipt)}
        onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocusedReceipt(undefined); }}>
        <h4 ref={receiptRef} tabIndex={-1}>{receipt.entry.action === "judgment_recorded" ? "Judgment recorded" : "Judgment revised"}</h4>
        <p><strong>{judgmentLabel(receipt.entry.after)}</strong></p>
        {((record.receipt && record.receipt.entry.entry_id !== receipt.entry.entry_id)
          || (judgmentVersion(canonical) ?? receipt.entry.after.judgment_revision) > receipt.entry.after.judgment_revision)
          && <p>Past saved acknowledgment. View the current judgment for its latest state.</p>}
        <p>{receipt.entry.action === "judgment_recorded" ? "Automated assessment retained separately." : "Previous judgment retained in history."}</p>
        <div className="signal-control-cluster"><Button variant="secondary" onClick={event => review.viewSaved(criterionId, event.currentTarget)}>View current judgment</Button>
          <Button variant="quiet" onClick={event => open("history", event.currentTarget)}>View judgment history</Button></div>
      </div>}
    </section>}
    {record?.historyOpen && <section id={`${id}-history`} className="signal-group-flow" aria-labelledby={`${id}-history-heading`}>
      <h4 id={`${id}-history-heading`} ref={historyHeading} tabIndex={-1}>Judgment history</h4>
      {(record.loading || record.historyLoading) && <p role="status">Loading judgment history.</p>}
      {record.historyError && <div role="alert"><p>{record.historyError}</p><Button variant="secondary" onClick={event => open("history", event.currentTarget)}>Retry judgment history</Button></div>}
      {record.history && <>
        {record.history.history_incomplete && <p>Earlier history may be incomplete. Unavailable records are not shown as verified judgments.</p>}
        {(record.historyError || (record.context && !scope.currentContext(record.context))) && <p>Last loaded history. Review the current judgment to load its latest history.</p>}
        {record.history.entries.length === 0 && !record.history.history_incomplete && <p>No recorded revisions yet.</p>}
        {record.history.entries.length === 0 && record.history.history_incomplete && <p>No complete history entries are available.</p>}
        <ol className="signal-result-list">{record.history.entries.map(entry => <li key={entry.entry_id} className="signal-result-card signal-group-flow">
          <h5>{entry.action === "initial_resolution" || entry.action === "judgment_recorded" ? "Initial recorded judgment" : "Judgment revised"}
            {entry.after.judgment_revision === record.history!.current.judgment_revision ? ": Current at time of this history read" : ""}</h5>
          {entry.before && <div><h6>Previous judgment</h6><Snapshot value={entry.before} /></div>}
          {entry.schema_version === 2 && <details><summary>Original automated assessment</summary>
            <p>{originalJudgmentLabel(entry.original_assessment)}</p>
            {(entry.original_assessment.reasoning || entry.original_assessment.reason) && <p>{entry.original_assessment.reasoning ?? entry.original_assessment.reason}</p>}
          </details>}
          <div><h6>Recorded judgment</h6><Snapshot value={entry.after} /></div>
        </li>)}</ol>
        {record.history.next_cursor && <Button variant="secondary" disabled={record.historyLoading || Boolean(record.historyError)}
          onClick={() => scope.history(criterionId, true)}>Show earlier revisions</Button>}
      </>}
    </section>}
    {confirmation && proposal && <Dialog title={confirmation.kind === "record" ? "Record your judgment?" : "Save revised judgment?"} initialFocusTitle
      onClose={sending ? undefined : () => setConfirmation(undefined)} returnFocusTarget={() => completion.returnTarget(receiptRef.current) ?? reviewRef.current}
      fallbackFocusRef={currentHeading} shouldRestoreFocus={() => completion.shouldRestore(scope.isCurrent)}
      actions={<div className="signal-judgment-confirm-actions"><ActionAssessmentContext id={`${id}-confirm-context`} notices={review.assessmentNotices ?? []} /><div className="signal-control-cluster"><Button variant="secondary" disabled={sending} onClick={() => setConfirmation(undefined)}>Back to editing</Button>
        <Button variant="brand" aria-describedby={confirmContextDescription} disabled={sending || !draft || !scope.canWrite(draft)} busy={sending} onClick={async event => {
          if (sending) return;
          completion.begin(event.currentTarget); setSending(true);
          const accepted = await scope.confirm(criterionId);
          if (!scope.isCurrent()) return;
          if (accepted) completion.accept();
          setSending(false); setConfirmation(undefined);
        }}>{confirmation.kind === "record" ? "Save judgment" : "Save revised judgment"}</Button></div></div>}>
      <div className="signal-group-flow" data-judgment-dialog={id}><p>{criterionText}</p><section><h3>{confirmation.kind === "record" ? "Automated assessment" : "Current judgment"}</h3>
        {confirmation.bound.current ? <Snapshot value={confirmation.bound.current} /> : <p>{originalJudgmentLabel(confirmation.bound.original_assessment)}</p>}</section>
        <section><h3>{confirmation.kind === "record" ? "Your judgment" : "Revised judgment"}</h3><p><strong>{judgmentLabel(proposal)}</strong></p><p><strong>New instructor reason:</strong> {confirmation.reason.trim()}</p>
          {proposal.resolution_type === "needs_document" && <p>{CRITERION_EXCLUSION_COPY.noGrade}</p>}</section>
        <p>{confirmation.kind === "record" ? "This records your instructor judgment and recalculates readiness. The automated assessment is retained separately. It does not rerun the original check." : "This replaces the current instructor judgment and recalculates readiness. The previous judgment and reason remain in history. It does not rerun the original check."}</p>
        {record?.abandonedAttempt && <p>{record.abandonedAttempt.kind === "record" ? "An earlier initial judgment save could not be confirmed. This action does not cancel the earlier request." : "An earlier revision save could not be confirmed. This is a new revision of the current judgment shown above. It does not cancel the earlier request."}</p>}
        {!draft || !scope.canWrite(draft) ? <p>{sending ? confirmation.kind === "record" ? "Saving judgment." : "Saving revision." : "This assessment changed. Go back to editing and review the current judgment."}</p> : null}
      </div>
    </Dialog>}
    {stopping && <Dialog title={stopping.kind === "record" ? "Stop editing this judgment?" : "Stop editing this revision?"} initialFocusTitle onClose={() => setStopping(undefined)}
      returnFocusTarget={() => currentHeading.current ?? document.getElementById(criterionRecordId(checkRunId, criterionId))
        ?? document.getElementById("criteria-results-heading")} shouldRestoreFocus={scope.isCurrent}
      actions={<><Button variant="secondary" onClick={() => setStopping(undefined)}>Keep draft</Button>
        <Button variant="danger" disabled={!scope.canStop()} onClick={() => {
          if (scope.abandon(criterionId, stopping)) setStopping(undefined);
          else setStopError("This review changed. Keep the draft and review its current state before stopping editing.");
        }}>Stop editing</Button></>}>
      <div className="signal-group-flow" data-judgment-dialog={id}><p>{criterionText}</p>
        <p>{stopping.kind === "record" ? "This removes this judgment's entered choice and reason from this review." : "This removes this revision's entered judgment and reason from this review."}</p>
        <p>The earlier save may already have completed or may still complete. Stopping editing does not cancel or reverse it.</p>
        <p>Review the current judgment before making another change.</p>
        {stopError && <p role="alert">{stopError}</p>}
      </div>
    </Dialog>}
    {discarding && <Dialog title={recording ? "Discard this unsaved judgment?" : "Discard this unsaved revision?"} initialFocusTitle onClose={() => setDiscarding(false)} fallbackFocusRef={currentHeading}
      actions={<><Button variant="secondary" onClick={() => setDiscarding(false)}>Keep editing</Button><Button variant="danger" onClick={event => followDiscard(event.currentTarget, () => {
        scope.discard(criterionId); setDiscarding(false);
      })}>{recording ? "Discard judgment" : "Discard revision"}</Button></>}><p data-judgment-dialog={id}>{recording ? "This discards only the unsaved judgment for this criterion." : "This discards only the unsaved revision for this criterion."}</p></Dialog>}
  </div>;
}
