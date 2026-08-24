// BUG-078/FEATURES.md §9 (screen 4i) — confirms a citation source
// VERIDICAL couldn't find in CrossRef/Semantic Scholar/Open Library/
// Google Books is nonetheless real (e.g. a local/Philippine source these
// providers don't index). A modal, not OverrideControl's inline expand
// (ui-designer spec, 2026-08-24): unlike an ordinary override, this is
// global (marks the shared citation_cache row so every OTHER instructor's
// manuscript citing the identical source stops being flagged too) and
// permanent (no un-confirm exists yet) — the hard rules already reserve a
// custom Modal for exactly this class of consequential confirmation.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { CitationSourceKeyOut, FlagOut } from "../api/types";
import { Modal, ModalBackdrop } from "../components/Modal";
import { cx } from "../components/cx";
import { useConfirmCitationSource } from "./useFlag";

const KEY_LABEL: Record<CitationSourceKeyOut["kind"], string> = {
  doi: "DOI",
  isbn: "ISBN",
  title: "title",
};

interface ConfirmCitationSourceModalProps {
  flag: FlagOut;
  onClose: () => void;
}

export function ConfirmCitationSourceModal({ flag, onClose }: ConfirmCitationSourceModalProps) {
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const confirm = useConfirmCitationSource(flag.id);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const reasonId = useId();
  const reasonErrId = useId();
  const key = flag.citation_source_key;

  useEffect(() => {
    if (confirm.isError) errorRef.current?.focus();
  }, [confirm.isError]);

  const invalid = attempted && !reason.trim();

  function handleConfirm() {
    setAttempted(true);
    if (!reason.trim()) return;
    confirm.mutate(reason.trim(), { onSuccess: () => onClose() });
  }

  const serverError =
    confirm.error instanceof ApiError
      ? confirm.error.message
      : confirm.error
        ? "Couldn't confirm this source. Try again."
        : null;

  return (
    <ModalBackdrop>
      <Modal
        title="Confirm this source is legitimate?"
        onClose={confirm.isPending ? undefined : onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              disabled={confirm.isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={confirm.isPending}
              aria-busy={confirm.isPending ? "true" : undefined}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {confirm.isPending ? "Confirming." : "Confirm this source"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3" aria-busy={confirm.isPending}>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
              Source
            </span>
            {/* BUG-078 (ux-critic finding, live-reproduced): an unbounded
                blockquote on a genuinely long evidence_excerpt pushed the
                mandatory disclosure, reason field, and Confirm/Cancel
                buttons off-screen -- capped and internally scrollable so
                the rest of the modal (and the decision itself) always
                stays reachable without scrolling past the whole quote. */}
            <blockquote className="max-h-32 overflow-y-auto rounded-md border border-border bg-page px-3 py-2 text-sm break-words text-ink">
              {flag.evidence_excerpt}
            </blockquote>
            {key?.kind === "title" ? (
              <p className="rounded-md border border-status-caution-text/25 bg-status-caution-bg px-3 py-2 text-xs text-status-caution-text">
                Matched by title, not a DOI or ISBN. A title match is less precise than an ID
                match, so double-check this is really the same source before confirming.
              </p>
            ) : (
              key && (
                <p className="text-xs text-ink-tertiary break-words">
                  Matched by {KEY_LABEL[key.kind]}: {key.value}
                </p>
              )
            )}
          </div>
          <p className="text-sm text-ink-secondary">
            Confirming this resolves this flag on this report, the same as an override.
            VERIDICAL's readiness score recalculates immediately.
          </p>
          <p className="text-sm text-ink-secondary">
            It also does one more thing. VERIDICAL checks this exact source, by its DOI, ISBN, or
            title, across every manuscript that cites it. Confirming it here marks the source
            itself as verified, so it will stop being flagged as unverifiable on{" "}
            <b>any other manuscript that cites it, from any instructor</b>, not just this one.
          </p>
          <p className="text-sm text-ink-secondary">
            <b>There's no way to undo this from within VERIDICAL yet.</b>
          </p>
          <div className="flex flex-col gap-1">
            <label htmlFor={reasonId} className="text-sm font-medium text-ink">
              Where you verified this (required)
            </label>
            <input
              id={reasonId}
              type="text"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                if (attempted) setAttempted(false);
              }}
              placeholder="e.g. publisher website, DOI resolver, institutional repository, physical copy"
              disabled={confirm.isPending}
              aria-invalid={invalid ? "true" : undefined}
              aria-describedby={invalid ? reasonErrId : undefined}
              className={cx(
                "h-11 rounded-md border px-3 text-base text-ink sm:h-9",
                invalid ? "border-2 border-status-attention-text" : "border-border-input",
              )}
            />
            {invalid && (
              <p id={reasonErrId} role="alert" className="text-sm text-status-attention-text">
                Enter where you verified this before confirming.
              </p>
            )}
          </div>
          {serverError && (
            <p ref={errorRef} role="alert" tabIndex={-1} className="text-sm font-medium text-danger">
              <span className="sr-only">Error: </span>
              {serverError}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
