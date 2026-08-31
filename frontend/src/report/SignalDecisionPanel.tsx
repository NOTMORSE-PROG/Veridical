import { useEffect, useRef, useState } from "react";
import type { Decision, ReportOut } from "../api/types";
import { INSTRUCTOR_NOTE_MAX_LENGTH, RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { DECISION_LABEL } from "../domain/decisionTone";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { ReadinessBand } from "../ui/ReadinessBand";
import { useDecideReport, useReopenReport } from "./useReport";

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

const DECISION_COPY: Record<Decision, { title: string; confirm: string; phrase: string }> = {
  approved: { title: "Approve for defense?", confirm: "Approve for defense", phrase: "approved for defense" },
  returned: { title: "Return for revision?", confirm: "Return for revision", phrase: "returned for revision" },
  rejected: { title: "Reject this manuscript?", confirm: "Reject manuscript", phrase: "rejected" },
};

const STATUSES_REQUIRING_REASON: Record<Decision, ReadonlySet<ReportOut["status"]>> = {
  approved: new Set(["not_ready", "conditionally_ready", "needs_review"]),
  rejected: new Set(["ready", "needs_review"]),
  returned: new Set(),
};

function DecisionDialog({ decision, report, manuscriptLabel, onClose }: {
  decision: Decision;
  report: ReportOut;
  manuscriptLabel: string;
  onClose: () => void;
}) {
  const [note, setNote] = useState("");
  const [attempted, setAttempted] = useState(false);
  const decide = useDecideReport(report.check_run_id);
  const copy = DECISION_COPY[decision];
  const reasonRequired = STATUSES_REQUIRING_REASON[decision].has(report.status);
  const invalid = attempted && reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH;

  function submit() {
    setAttempted(true);
    if (reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH) return;
    decide.mutate({ decision, note: note.trim() || null }, { onSuccess: onClose });
  }

  return (
    <Dialog
      title={copy.title}
      onClose={decide.isPending ? undefined : onClose}
      actions={<><Button variant="secondary" disabled={decide.isPending} onClick={onClose}>Cancel</Button><Button variant={decision === "rejected" ? "danger" : "brand"} busy={decide.isPending} onClick={submit}>{copy.confirm}</Button></>}
    >
      <div className="signal-decision-dialog" aria-busy={decide.isPending}>
        <div className="signal-decision-dialog__band"><span>VERIDICAL readiness band</span><ReadinessBand status={report.status} /></div>
        <p>This records {manuscriptLabel} as {copy.phrase}. The report becomes read-only until an explicit, reasoned reopen.</p>
        <label><span>{reasonRequired ? "Reason (required)" : "Note (optional)"}</span><textarea rows={4} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH} disabled={decide.isPending} value={note} aria-invalid={invalid || undefined} aria-describedby={invalid ? "signal-decision-reason-error" : "signal-decision-note-hint"} onChange={(event) => { setNote(event.target.value); if (attempted) setAttempted(false); }} /></label>
        <p id="signal-decision-note-hint" className="signal-field-hint">{reasonRequired ? "This choice differs from VERIDICAL's readiness band, so explain your judgment. " : ""}The note is saved in Audit and appears to people who receive an active share link.</p>
        <p className="signal-field-hint">{note.length} of {INSTRUCTOR_NOTE_MAX_LENGTH} characters</p>
        {invalid && <p id="signal-decision-reason-error" role="alert" className="signal-field-error">{note.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before confirming."}</p>}
        {Boolean(decide.error) && <Alert title="Could not record this decision" tone="error" role="alert">{decide.error instanceof Error ? decide.error.message : "Try again."}</Alert>}
      </div>
    </Dialog>
  );
}

function ReopenDialog({ report, onClose }: { report: ReportOut; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const reopen = useReopenReport(report.check_run_id);
  const invalid = attempted && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;

  function submit() {
    setAttempted(true);
    if (reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) return;
    reopen.mutate(reason.trim(), { onSuccess: onClose });
  }

  return (
    <Dialog
      title="Reopen this decision?"
      onClose={reopen.isPending ? undefined : onClose}
      actions={<><Button variant="secondary" disabled={reopen.isPending} onClick={onClose}>Keep decision</Button><Button variant="brand" busy={reopen.isPending} onClick={submit}>Reopen decision</Button></>}
    >
      <div className="signal-decision-dialog"><p>The current “{report.decision ? DECISION_LABEL[report.decision] : "decision"}” record stays in Audit. Reopening returns this report to the instructor decision gate.</p><label><span>Reason (required)</span><textarea autoFocus rows={4} maxLength={INSTRUCTOR_NOTE_MAX_LENGTH} disabled={reopen.isPending} value={reason} aria-invalid={invalid || undefined} aria-describedby={invalid ? "signal-reopen-error" : undefined} onChange={(event) => { setReason(event.target.value); if (attempted) setAttempted(false); }} /></label>{invalid && <p id="signal-reopen-error" role="alert" className="signal-field-error">{reason.trim() ? `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters.` : "Enter a reason before reopening."}</p>}{Boolean(reopen.error) && <Alert title="Could not reopen this decision" tone="error" role="alert">{reopen.error instanceof Error ? reopen.error.message : "Try again."}</Alert>}</div>
    </Dialog>
  );
}

export function SignalDecisionPanel({ report, manuscriptLabel }: { report: ReportOut; manuscriptLabel: string }) {
  const [openDecision, setOpenDecision] = useState<Decision>();
  const [reopen, setReopen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousDecision = useRef(report.decision);
  const blocked = report.decision === null && report.pending_review_count > 0;

  useEffect(() => {
    if (previousDecision.current !== report.decision) headingRef.current?.focus();
    previousDecision.current = report.decision;
  }, [report.decision]);

  return (
    <section id="final-decision" tabIndex={-1} className="signal-decision-gate" aria-labelledby="decision-heading">
      <div className="signal-decision-gate__heading"><div><p className="signal-section-kicker">Decide · Instructor authority</p><h2 id="decision-heading" ref={headingRef} tabIndex={-1}>Final defense-readiness decision</h2></div>{report.decision && <span className={`signal-decision signal-decision--${report.decision}`}>{DECISION_LABEL[report.decision]}</span>}</div>

      {!report.rubric_is_current && <Alert title="A newer format version is active" tone="warning">This report remains pinned to the format used when the check ran. Start a new check if the newer version should govern your decision.</Alert>}

      {report.decision ? (
        <div className="signal-decision-record"><p>Recorded by you{report.decided_at ? ` on ${dateFormatter.format(new Date(report.decided_at))}` : ""}. This is the instructor's decision, not an automated approval or block.</p>{report.decision_note && <blockquote><strong>Decision note</strong><span>{report.decision_note}</span></blockquote>}<Button variant="secondary" onClick={() => setReopen(true)}>Reopen this decision</Button></div>
      ) : (
        <div className="signal-decision-choice"><p>VERIDICAL provides a readiness band and checkable evidence. You make the final decision after reviewing the full record above.</p>{blocked && <Alert title={`${report.pending_review_count} ${report.pending_review_count === 1 ? "criterion needs" : "criteria need"} your judgment`} tone="warning"><a href="#review-criteria">Resolve {report.pending_review_count === 1 ? "it" : "them"} before deciding.</a></Alert>}<div className="signal-decision-choice__actions"><Button variant="brand" disabled={blocked} onClick={() => setOpenDecision("approved")}>Approve for defense</Button><Button variant="secondary" disabled={blocked} onClick={() => setOpenDecision("returned")}>Return for revision</Button><Button variant="danger" disabled={blocked} onClick={() => setOpenDecision("rejected")}>Reject manuscript</Button></div></div>
      )}

      {openDecision && <DecisionDialog decision={openDecision} report={report} manuscriptLabel={manuscriptLabel} onClose={() => setOpenDecision(undefined)} />}
      {reopen && <ReopenDialog report={report} onClose={() => setReopen(false)} />}
    </section>
  );
}
