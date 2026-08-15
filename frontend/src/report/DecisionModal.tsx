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
import { READINESS_LABEL, READINESS_TONE } from "../domain/readinessTone";
import { useDecideReport } from "./useReport";

const NOTE_MAX_LENGTH = 1000;

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
  const decide = useDecideReport(report.check_run_id);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const noteId = useId();
  const noteHintId = useId();
  const noteCounterId = useId();
  const copy = COPY[decision];

  useEffect(() => {
    if (decide.isError) errorRef.current?.focus();
  }, [decide.isError]);

  function handleConfirm() {
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
              Note (optional)
            </label>
            {/* Hint and counter kept OUTSIDE the label/via aria-describedby
                rather than nested inside a <label> -- a nested counter
                would fold "0 / 1000" into the field's own accessible
                name and change it on every keystroke (the exact bug
                ReopenModal's own reason field avoids for the same
                reason). */}
            <p id={noteHintId} className="text-xs text-ink-tertiary">
              Saved with this decision and visible in the audit trail, and to anyone
              you give a share link to this report.
            </p>
            <textarea
              id={noteId}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              maxLength={NOTE_MAX_LENGTH}
              disabled={decide.isPending}
              aria-describedby={`${noteHintId} ${noteCounterId}`}
              className="rounded-md border border-border-input px-3 py-2 text-base text-ink"
            />
            <p id={noteCounterId} className="text-xs text-ink-tertiary">
              {note.length} / {NOTE_MAX_LENGTH}
            </p>
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
