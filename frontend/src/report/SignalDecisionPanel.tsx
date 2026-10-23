import { useContext, useId, useRef, useState, type RefObject } from "react";
import type { Decision, ReportOut } from "../api/types";
import { INSTRUCTOR_NOTE_MAX_LENGTH, RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { DECISION_LABEL } from "../domain/decisionTone";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { ReadinessBand } from "../ui/ReadinessBand";
import { isAssessmentRevision } from "./assessmentRevision";
import { ReviewContext } from "./criterionReviewContext";
import { DecisionReadRecovery, type ReportReadRecovery } from "./ReportReadNotice";
import { resultDisplay } from "./ResultsTable";
import { useDecisionReview } from "./useDecisionReview";
import { useDecideReport, useReopenReport } from "./useReport";
import { useExplicitActionFocus } from "../routing/useExplicitActionFocus";

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const DECISION_COPY: Record<Decision, { title: string; confirm: string; phrase: string }> = {
  approved: { title: "Approve for defense?", confirm: "Approve for defense", phrase: "approved for defense" },
  returned: { title: "Return for revision?", confirm: "Return for revision", phrase: "returned for revision" },
  rejected: { title: "Reject this manuscript?", confirm: "Reject manuscript", phrase: "rejected" },
};
const STATUSES_REQUIRING_REASON: Record<Decision, ReadonlySet<ReportOut["status"]>> = {
  approved: new Set(["not_ready", "conditionally_ready", "needs_review"]),
  rejected: new Set(["ready", "needs_review"]), returned: new Set(),
};

function CurrentAssessmentReview({ review, busy, onRebind }: { review: ReturnType<typeof useDecisionReview>; busy: boolean; onRebind: (trigger: HTMLElement) => void }) {
  if (review.canSubmit) return null;
  return <div className="signal-copy-flow">
    <p>The assessment changed or needs a current read. Your reason is kept. Review its current record before confirming.</p>
    <Button variant="secondary" disabled={busy || review.reading} busy={review.reading} onClick={() => { void review.reviewCurrent(); }}>Review current assessment</Button>
    {review.error && <p role="alert">{review.error}</p>}
    {review.reviewed && <div className="signal-copy-flow"><h3>Current assessment</h3>
      <ReadinessBand status={review.reviewed.status} />
      <p>{review.reviewed.pending_review_count} criteria remain unresolved. Instructor decision: {review.reviewed.decision ? DECISION_LABEL[review.reviewed.decision] : "Not recorded"}.</p>
      <ul>{review.reviewed.results.map(row => <li key={row.criterion_id}>{row.text}: {resultDisplay(row).label}</li>)}</ul>
      {review.canRebind && <Button variant="secondary" disabled={busy} onClick={event => onRebind(event.currentTarget)}>Use my reason with this current assessment</Button>}
    </div>}
  </div>;
}

type DialogOutcome = (report: ReportOut) => void;
function DecisionDialog({ decision, report, manuscriptLabel, onClose, onSaved, fallbackFocusRef, readFailed, retrying, onRetry }: ReportReadRecovery & {
  decision: Decision; report: ReportOut; manuscriptLabel: string; onClose: () => void;
  onSaved: DialogOutcome; fallbackFocusRef: RefObject<HTMLHeadingElement | null>;
}) {
  const [note, setNote] = useState("");
  const [attempted, setAttempted] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const followAction = useExplicitActionFocus(() => reasonRef.current);
  const errorId = useId();
  const decide = useDecideReport(report.check_run_id);
  const review = useDecisionReview(report, decision, readFailed);
  const drafts = useContext(ReviewContext);
  const copy = DECISION_COPY[decision];
  const pendingReturn = decision === "returned" && report.pending_review_count > 0;
  const reasonRequired = pendingReturn || STATUSES_REQUIRING_REASON[decision].has(report.status);
  const invalid = attempted && reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  function submit() {
    if (!review.isCurrent() || !review.canSubmit || !isAssessmentRevision(review.expectedRevision)) return;
    setAttempted(true);
    if (reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH) { reasonRef.current?.focus(); return; }
    decide.mutate({ decision, note: note.trim() || null, expected_revision: review.expectedRevision }, {
      onSuccess: saved => { if (review.isCurrent()) { onSaved(saved); onClose(); } }, onError: review.freeze,
    });
  }
  return <Dialog title={copy.title} initialFocusTitle onClose={decide.isPending ? undefined : onClose} fallbackFocusRef={fallbackFocusRef}
    actions={<><Button variant="secondary" disabled={decide.isPending} onClick={onClose}>Cancel</Button>
      <Button variant={decision === "rejected" ? "danger" : "brand"} disabled={!review.canSubmit} busy={decide.isPending} onClick={submit}>{decide.isPending && decision === "returned" ? "Recording return" : copy.confirm}</Button></>}>
    <div className="signal-decision-dialog" aria-busy={decide.isPending}>
      <div className="signal-decision-dialog__band"><span>{readFailed ? "Last loaded readiness band" : "VERIDICAL readiness band"}</span><ReadinessBand status={report.status} /></div>
      {readFailed && <DecisionReadRecovery retrying={retrying} onRetry={onRetry} />}
      <CurrentAssessmentReview review={review} busy={decide.isPending} onRebind={trigger => followAction(trigger, review.rebind)} />
      <p>This records {manuscriptLabel} as {copy.phrase}{pendingReturn ? ` with ${report.pending_review_count} unresolved criteria. Returning does not grade or exclude them.` : "."} The report becomes read-only until you reopen it with a reason.</p>
      {drafts && drafts.drafts.size > 0 && <p>Your criterion drafts stay unsaved and locked. After reopening, review each current criterion before using its draft.</p>}
      <label><span>{pendingReturn ? "Reason for return (required)" : reasonRequired ? "Reason (required)" : "Note (optional)"}</span>
        <textarea ref={reasonRef} rows={4} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH} disabled={decide.isPending} value={note}
          aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : "signal-decision-note-hint"}
          onChange={event => { setNote(event.target.value); setAttempted(false); }} /></label>
      <p id="signal-decision-note-hint" className="signal-field-hint">{pendingReturn ? "Explain what needs revision and which judgments remain incomplete. " : reasonRequired ? "This choice differs from VERIDICAL's readiness band, so explain your judgment. " : ""}Recorded in Audit and shown in the report, PDF and active share link.</p>
      <p className="signal-field-hint">{note.length} of {INSTRUCTOR_NOTE_MAX_LENGTH} characters</p>
      {invalid && <p id={errorId} role="alert" className="signal-field-error">{note.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters and explain your judgment.` : pendingReturn ? "Enter a reason for returning the manuscript with unresolved criteria." : "Enter a reason before confirming."}</p>}
      {decide.isError && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current assessment before trying again.</Alert>}
    </div>
  </Dialog>;
}

function ReopenDialog({ report, onClose, onSaved, fallbackFocusRef, readFailed, retrying, onRetry }: ReportReadRecovery & {
  report: ReportOut; onClose: () => void; onSaved: DialogOutcome; fallbackFocusRef: RefObject<HTMLHeadingElement | null>;
}) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const errorId = useId();
  const reopen = useReopenReport(report.check_run_id);
  const followAction = useExplicitActionFocus(() => reasonRef.current);
  const review = useDecisionReview(report, "reopen", readFailed);
  const invalid = attempted && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  function submit() {
    if (!review.isCurrent() || !review.canSubmit || !isAssessmentRevision(review.expectedRevision)) return;
    setAttempted(true);
    if (reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) { reasonRef.current?.focus(); return; }
    reopen.mutate({ reason: reason.trim(), expected_revision: review.expectedRevision }, {
      onSuccess: saved => { if (review.isCurrent()) { onSaved(saved); onClose(); } }, onError: review.freeze,
    });
  }
  return <Dialog title="Reopen this decision?" initialFocusTitle onClose={reopen.isPending ? undefined : onClose} fallbackFocusRef={fallbackFocusRef}
    actions={<><Button variant="secondary" disabled={reopen.isPending} onClick={onClose}>Keep decision</Button>
      <Button variant="brand" disabled={!review.canSubmit} busy={reopen.isPending} onClick={submit}>Reopen decision</Button></>}>
    <div className="signal-decision-dialog">
      {readFailed && <DecisionReadRecovery retrying={retrying} onRetry={onRetry} />}
      <CurrentAssessmentReview review={review} busy={reopen.isPending} onRebind={trigger => followAction(trigger, review.rebind)} />
      <p>The current decision stays in Audit. Reopening permits further instructor review. It does not confirm or save earlier unsaved drafts; review each current criterion before using its draft.</p>
      <label><span>Reason (required)</span><textarea ref={reasonRef} rows={4} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH} disabled={reopen.isPending} value={reason}
        aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : undefined}
        onChange={event => { setReason(event.target.value); setAttempted(false); }} /></label>
      {invalid && <p id={errorId} role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before reopening."}</p>}
      {reopen.isError && <Alert title="Saving could not be confirmed" tone="error" role="alert">Your entered reason is kept. Review the current assessment before trying again.</Alert>}
    </div>
  </Dialog>;
}

export function SignalDecisionPanel({ report, manuscriptLabel, readFailed, retrying, onRetry }: ReportReadRecovery & { report: ReportOut; manuscriptLabel: string }) {
  const [openDecision, setOpenDecision] = useState<Decision>();
  const [reopen, setReopen] = useState(false);
  const [receipt, setReceipt] = useState<ReportOut>();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const pending = report.pending_review_count > 0;
  const unavailable = readFailed || !isAssessmentRevision(report.assessment_revision);
  function saved(current: ReportOut) { setReceipt(current); }
  return <section id="final-decision" tabIndex={-1} className="signal-decision-gate" aria-labelledby="decision-heading">
    <div className="signal-decision-gate__heading"><div><p className="signal-section-kicker">Decide - Instructor authority</p><h2 id="decision-heading" ref={headingRef} tabIndex={-1}>Final defense-readiness decision</h2></div>{report.decision && <span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span>}</div>
    {receipt && <p role="status" className="signal-decision-gate__receipt">{receipt.decision === "returned" ? `Return for revision recorded. ${receipt.pending_review_count} criteria remain unresolved.` : receipt.decision === null ? "Decision reopened. Earlier drafts still need current review before confirmation." : `${DECISION_LABEL[receipt.decision]} recorded.`}</p>}
    {readFailed && <DecisionReadRecovery retrying={retrying} onRetry={onRetry} />}
    {!report.rubric_is_current && <Alert title="A newer format version is active" tone="warning">This report remains pinned to the format used when the check ran. Start a new check if the newer version should govern your decision.</Alert>}
    {report.decision ? <div className="signal-decision-record">
      <p>Recorded by you{report.decided_at ? ` on ${dateFormatter.format(new Date(report.decided_at))}` : ""}. This is the instructor's decision, not an automated approval or block.</p>
      {report.decision === "returned" && pending && <p>{report.pending_review_count} criteria remain unresolved. Their original outcomes and assessment limits are preserved. Reopen this decision before making further judgments.</p>}
      {report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}
      <Button variant="secondary" disabled={unavailable} onClick={() => setReopen(true)}>Reopen this decision</Button>
    </div> : <div className="signal-decision-choice">
      <p>VERIDICAL provides a readiness band and checkable evidence. You make the final decision after reviewing the full record above.</p>
      {pending && <Alert title={`${report.pending_review_count} ${report.pending_review_count === 1 ? "criterion needs" : "criteria need"} your judgment`} tone="warning">You can return this manuscript for revision with a reason. Returning does not grade or exclude these criteria. Approval remains unavailable. <a href="#review-criteria">Review unresolved criteria.</a></Alert>}
      <div className="signal-decision-choice__actions">
        <Button variant="brand" disabled={pending || unavailable} onClick={() => setOpenDecision("approved")}>Approve for defense</Button>
        <Button variant="secondary" disabled={unavailable} onClick={() => setOpenDecision("returned")}>Return for revision</Button>
        <Button variant="danger" disabled={pending || unavailable} onClick={() => setOpenDecision("rejected")}>Reject manuscript</Button>
      </div>
    </div>}
    {openDecision && <DecisionDialog key={`${report.check_run_id}:${openDecision}`} decision={openDecision} report={report} manuscriptLabel={manuscriptLabel} readFailed={readFailed} retrying={retrying} onRetry={onRetry} onClose={() => setOpenDecision(undefined)} onSaved={saved} fallbackFocusRef={headingRef} />}
    {reopen && <ReopenDialog key={`${report.check_run_id}:reopen`} report={report} readFailed={readFailed} retrying={retrying} onRetry={onRetry} onClose={() => setReopen(false)} onSaved={saved} fallbackFocusRef={headingRef} />}
  </section>;
}
