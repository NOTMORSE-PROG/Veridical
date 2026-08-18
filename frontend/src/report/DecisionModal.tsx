// V-038 (F8.5, screen 4j) — confirms one of the three terminal decisions.
// Parameterized by which decision was clicked (not a chooser inside the
// modal): the choice is already made by which peer button opened it, and
// re-presenting a picker here would add a redundant click for no
// accuracy gain (Hick's law) while contradicting what a confirm dialog
// is for -- confirming an already-selected action, not re-deciding it.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Decision, ReportOut } from "../api/types";
import { Modal, ModalBackdrop } from "../components/Modal";
import { StatusPill } from "../components/StatusPill";
import { cx } from "../components/cx";
import { READINESS_LABEL, READINESS_TONE } from "../domain/readinessTone";
import { RESOLUTION_REASON_MIN_LENGTH } from "./EscalatedPanel";
import { useDecideReport } from "./useReport";

const NOTE_MAX_LENGTH = 1000;

// BUG-095: mirrors `service.py`'s `DECISIONS_REQUIRING_A_REASON` -- a
// client-side pre-check so the instructor sees the requirement before
// submitting, not just after a rejected request. The server re-checks
// authoritatively regardless (this is UX, not the security boundary).
// Required only when the decision DISAGREES with VERIDICAL's own
// computed verdict -- approving a `not_ready`/`conditionally_ready`/
// `needs_review` report, or rejecting a `ready`/`needs_review` one.
// `needs_review` means nothing was computed at all, so EITHER decision on
// it is pure human judgment with zero AI signal behind it (backend-critic
// finding, live-reproduced: this was missing entirely). "Returned" is
// neither an approval nor a rejection, so it never requires one.
const STATUSES_REQUIRING_A_REASON: Record<Decision, ReadonlySet<string>> = {
  approved: new Set(["not_ready", "conditionally_ready", "needs_review"]),
  rejected: new Set(["ready", "needs_review"]),
  returned: new Set(),
};

const COPY: Record<
  Decision,
  { title: string; statePhrase: string; confirmLabel: string; confirmingLabel: string }
> = {
  approved: {
    title: "Approve for defense?",
    statePhrase: "approved for defense",
    confirmLabel: "Approve for defense",
    confirmingLabel: "Approving.",
  },
  returned: {
    title: "Return for revision?",
    statePhrase: "returned for revision",
    confirmLabel: "Return for revision",
    confirmingLabel: "Returning.",
  },
  rejected: {
    title: "Reject?",
    statePhrase: "rejected",
    confirmLabel: "Reject",
    confirmingLabel: "Rejecting.",
  },
};

interface DecisionModalProps {
  decision: Decision;
  report: ReportOut;
  manuscriptLabel: string;
  onClose: () => void;
}

export function DecisionModal({ decision, report, manuscriptLabel, onClose }: DecisionModalProps) {
  const [note, setNote] = useState("");
  const [attempted, setAttempted] = useState(false);
  const decide = useDecideReport(report.check_run_id);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const noteId = useId();
  const noteHintId = useId();
  const noteCounterId = useId();
  const noteErrId = useId();
  const copy = COPY[decision];
  const reasonRequired = STATUSES_REQUIRING_A_REASON[decision].has(report.status);
  // BUG-095 follow-up (newcomer/backend-critic finding, live-reproduced):
  // this used to check presence only, so "ok" satisfied it -- the
  // escalation-resolution reason (BUG-096) enforces a real minimum on the
  // exact same class of published justification; this is the SAME
  // control on the higher-stakes action and had a lower bar than the one
  // it trained the instructor to expect two clicks earlier.
  const reasonInvalid = attempted && reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH;

  useEffect(() => {
    if (decide.isError) errorRef.current?.focus();
  }, [decide.isError]);

  function handleConfirm() {
    setAttempted(true);
    if (reasonRequired && note.trim().length < RESOLUTION_REASON_MIN_LENGTH) return;
    decide.mutate(
      { decision, note: note.trim() ? note.trim() : null },
      { onSuccess: () => onClose() },
    );
  }

  const serverError =
    decide.error instanceof ApiError
      ? decide.error.message
      : decide.error
        ? "Could not record this decision. Please try again."
        : null;

  return (
    <ModalBackdrop>
      <Modal
        title={copy.title}
        onClose={decide.isPending ? undefined : onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              disabled={decide.isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={decide.isPending}
              aria-busy={decide.isPending ? "true" : undefined}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {decide.isPending ? copy.confirmingLabel : copy.confirmLabel}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3" aria-busy={decide.isPending}>
          {/* Re-states the report's own verdict at the moment of
              commitment, not just up in the page header where it may
              have scrolled out of view on a long report (recognition
              over recall, Nielsen heuristic #6) -- agreeing and
              verifying should take the same effort, not ask the
              instructor to trust their memory of a number they read
              several screens ago. */}
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-page px-3 py-2">
            <StatusPill tone={READINESS_TONE[report.status]}>{READINESS_LABEL[report.status]}</StatusPill>
            {report.composite_score !== null && (
              <span className="text-sm font-bold text-ink">{report.composite_score}%</span>
            )}
          </div>
          <p className="text-sm text-ink-secondary">
            This marks {manuscriptLabel}'s report as {copy.statePhrase}. It becomes read only until
            you reopen it.
          </p>
          <div className="flex flex-col gap-1">
            <label htmlFor={noteId} className="text-sm font-medium text-ink">
              {reasonRequired ? "Reason (required)" : "Note (optional)"}
            </label>
            {/* Hint and counter kept OUTSIDE the label/via aria-describedby
                rather than nested inside a <label> -- a nested counter
                would fold "0 / 1000" into the field's own accessible
                name and change it on every keystroke (the exact bug
                ReopenModal's own reason field avoids for the same
                reason). */}
            <p id={noteHintId} className="text-xs text-ink-tertiary">
              {/* BUG-095: every OTHER instructor justification on this
                  product is required (overriding a flag, resolving an
                  escalation, reopening a decision) -- this was the one
                  exception, optional even when it overruled the system's
                  own verdict. Required only when this decision DISAGREES
                  with what VERIDICAL computed; still optional when they
                  agree. */}
              {reasonRequired
                ? `This disagrees with VERIDICAL's own ${READINESS_LABEL[report.status].toLowerCase()} ` +
                  "verdict, so explain why. Saved with this decision and visible in the audit " +
                  "trail, and to anyone you give a share link to this report."
                : "Saved with this decision and visible in the audit trail, and to anyone " +
                  "you give a share link to this report."}
            </p>
            <textarea
              id={noteId}
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
                if (attempted) setAttempted(false);
              }}
              rows={3}
              maxLength={NOTE_MAX_LENGTH}
              disabled={decide.isPending}
              aria-invalid={reasonInvalid ? "true" : undefined}
              aria-describedby={
                reasonInvalid
                  ? `${noteHintId} ${noteCounterId} ${noteErrId}`
                  : `${noteHintId} ${noteCounterId}`
              }
              className={cx(
                "rounded-md border px-3 py-2 text-base text-ink",
                reasonInvalid ? "border-2 border-status-attention-text" : "border-border-input",
              )}
            />
            <p id={noteCounterId} className="text-xs text-ink-tertiary">
              {note.length} / {NOTE_MAX_LENGTH}
            </p>
            {reasonInvalid && (
              <p id={noteErrId} className="text-sm text-status-attention-text">
                {note.trim()
                  ? `Reason must be at least ${RESOLUTION_REASON_MIN_LENGTH} characters -- this appears in the report, the exported PDF, and any share link.`
                  : "Enter a reason before confirming."}
              </p>
            )}
          </div>
          {serverError && (
            <p
              ref={errorRef}
              role="alert"
              tabIndex={-1}
              className="text-sm font-medium text-danger"
            >
              <span className="sr-only">Error: </span>
              {serverError}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
