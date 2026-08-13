// V-038 — undoes a decision so a new one can be made. Always explicit,
// always reasoned: same rigor as the mandatory-reason fields already
// established for escalation resolution (EscalatedPanel.tsx) and flag
// override (FlagDetail.tsx), extended one level up to the whole-report
// decision.
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Decision } from "../api/types";
import { Modal, ModalBackdrop } from "../components/Modal";
import { cx } from "../components/cx";
import { DECISION_LABEL } from "../domain/decisionTone";
import { useReopenReport } from "./useReport";

interface ReopenModalProps {
  decision: Decision;
  checkRunId: number;
  onClose: () => void;
}

export function ReopenModal({ decision, checkRunId, onClose }: ReopenModalProps) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const reopen = useReopenReport(checkRunId);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (reopen.isError) errorRef.current?.focus();
  }, [reopen.isError]);

  function handleConfirm() {
    setAttempted(true);
    if (!reason.trim()) {
      reasonRef.current?.focus();
      return;
    }
    reopen.mutate(reason.trim(), { onSuccess: () => onClose() });
  }

  const serverError =
    reopen.error instanceof ApiError
      ? reopen.error.message
      : reopen.error
        ? "Could not reopen this report. Please try again."
        : null;
  const invalid = attempted && !reason.trim();

  return (
    <ModalBackdrop>
      <Modal
        title="Reopen this report?"
        onClose={reopen.isPending ? undefined : onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              disabled={reopen.isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={reopen.isPending}
              aria-busy={reopen.isPending ? "true" : undefined}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {reopen.isPending ? "Reopening." : "Reopen report"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3" aria-busy={reopen.isPending}>
          <p className="text-sm text-ink-secondary">
            This report is currently marked {DECISION_LABEL[decision]}. Reopening removes that
            decision so you can make a new one. The previous decision stays in the audit trail.
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-sm font-medium text-ink">Reason (required)</span>
            <textarea
              ref={reasonRef}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                if (attempted) setAttempted(false);
              }}
              rows={3}
              maxLength={1000}
              disabled={reopen.isPending}
              aria-invalid={invalid ? "true" : undefined}
              aria-describedby={invalid ? "reopen-reason-err" : undefined}
              className={cx(
                "rounded-md border px-3 py-2 text-base text-ink",
                invalid ? "border-2 border-status-attention-text" : "border-border-input",
              )}
            />
          </label>
          {/* Kept OUTSIDE the <label>: nesting it inside would fold this
              text into the textarea's own accessible name once invalid,
              corrupting "Reason (required)" into a moving target every
              consumer of getByLabelText (real or assistive-tech) has to
              re-derive (same pitfall NewCheck.tsx's own comment already
              documents for a near-identical case). */}
          {invalid && (
            <p id="reopen-reason-err" className="text-sm text-status-attention-text">
              Enter a reason before reopening this report.
            </p>
          )}
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
