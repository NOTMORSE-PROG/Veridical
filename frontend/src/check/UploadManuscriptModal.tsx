// V-059 — the manuscript half of "uploads a required format... and a
// manuscript" (the product's own one-line description). The format half
// has had a real screen since V-012 (UploadRubricModal.tsx, screen 4c);
// this is the same shape pointed at the ingestion endpoint instead --
// same Modal base, same file-input/error pattern, one extra optional
// field (group label). No mandatory review screen follows this one (a
// manuscript has nothing to confirm the way a parsed rubric does), so
// unlike the rubric modal this one shows a real success state in place
// rather than auto-navigating away.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { UNGROUPED_LABEL } from "../domain/manuscriptLabel";
import { useIngestManuscript } from "./useCheckRun";

interface UploadManuscriptModalProps {
  onClose: () => void;
  /** Fired once a real upload succeeds, with the new manuscript's id --
   * lets the caller hand off straight into "New check" with this
   * manuscript preselected (Flow B, FEATURES.md), without forcing a
   * second round trip through the picker. */
  onUploadSuccess: (manuscriptId: number) => void;
}

function UploadIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 15V4M12 4l-4 4M12 4l4 4" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </svg>
  );
}

function DocIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="5" y1="5" x2="19" y2="19" />
      <line x1="19" y1="5" x2="5" y2="19" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

export function UploadManuscriptModal({ onClose, onUploadSuccess }: UploadManuscriptModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [groupLabel, setGroupLabel] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const ingest = useIngestManuscript();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const fileInputId = useId();
  const hintId = useId();
  const errorId = useId();
  const groupLabelId = useId();
  const groupHintId = useId();

  // Same BUG-028 rationale as UploadRubricModal: a focused element that
  // becomes `disabled` mid-pending drops focus to <body>. Moving focus to
  // the error message on failure is the recovery path, not a proactive
  // guard (this modal has no field that's ever focused-then-disabled on
  // the error path itself).
  useEffect(() => {
    if (ingest.isError) errorRef.current?.focus();
  }, [ingest.isError]);

  function handleUpload() {
    setFieldError(null);
    if (!file) {
      setFieldError("Choose a manuscript file before uploading.");
      return;
    }
    ingest.mutate({ file, groupLabel });
  }

  function handleUploadAnother() {
    setFile(null);
    setGroupLabel("");
    ingest.reset();
    fileInputRef.current?.focus();
  }

  const serverError =
    ingest.error instanceof ApiError
      ? ingest.error.message
      : ingest.error
        ? "Could not reach VERIDICAL to upload this file. Check your connection and try again."
        : null;
  const message = fieldError ?? serverError;
  const summary = ingest.data;

  const showsImageOnlyNote = summary?.image_only && summary.notes[0];
  const showsVisionUnavailable =
    summary?.vision_status === "unavailable" &&
    summary.images + summary.tables + summary.equations > 0;

  return (
    <ModalBackdrop>
      <Modal
        title="Upload manuscript"
        onClose={ingest.isPending ? undefined : onClose}
        footer={
          summary ? (
            <>
              <button
                type="button"
                onClick={handleUploadAnother}
                className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
              >
                Upload another
              </button>
              <button
                type="button"
                onClick={() => onUploadSuccess(summary.manuscript_id)}
                className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
              >
                Start a check with this manuscript
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={onClose}
                disabled={ingest.isPending}
                className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleUpload}
                disabled={ingest.isPending}
                aria-busy={ingest.isPending ? "true" : undefined}
                className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
              >
                {ingest.isPending ? "Uploading." : "Upload manuscript"}
              </button>
            </>
          )
        }
      >
        {summary ? (
          <div className="flex flex-col gap-3">
            <p className="rounded-md bg-status-success-bg px-3 py-2 text-sm text-status-success-text">
              {summary.citations > 0
                ? `Uploaded. ${summary.page_count} page(s) parsed, ${summary.citations} citation(s) found.`
                : `Uploaded. ${summary.page_count} page(s) parsed. No citations were found.`}
            </p>
            {showsImageOnlyNote && (
              <p className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
                {summary.notes[0]}
              </p>
            )}
            {showsVisionUnavailable && (
              <p className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
                VERIDICAL could not analyze the images in this document right now (the image
                reading model is unavailable). Checks that depend on image content may be
                limited.
              </p>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-4" aria-busy={ingest.isPending}>
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink">Manuscript file</span>
              <p id={hintId} className="text-sm text-ink-secondary">
                PDF or DOCX.
              </p>

              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <input
                  ref={fileInputRef}
                  id={fileInputId}
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  disabled={ingest.isPending}
                  aria-describedby={message ? `${hintId} ${errorId}` : hintId}
                  aria-invalid={message ? true : undefined}
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null);
                    setFieldError(null);
                    ingest.reset();
                  }}
                  className="file-trigger-input sr-only"
                />
                <label
                  htmlFor={fileInputId}
                  className={
                    ingest.isPending
                      ? "inline-flex h-11 w-full cursor-not-allowed items-center justify-center gap-2 rounded-md border border-border-disabled bg-panel px-4 text-sm font-bold text-ink-tertiary opacity-60 sm:w-auto"
                      : message
                        ? "inline-flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-md border-2 border-danger bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg sm:w-auto"
                        : "inline-flex h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg sm:w-auto"
                  }
                >
                  <UploadIcon />
                  Choose file
                </label>

                {!file && <span className="text-sm text-ink-tertiary">No file chosen yet.</span>}
              </div>
            </div>

            {file && (
              <div className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-page px-3 py-2 text-sm">
                <span className="flex-none text-ink-tertiary">
                  <DocIcon />
                </span>
                <span className="min-w-0 flex-1 truncate font-medium text-ink" title={file.name}>
                  {file.name}
                </span>
                <span className="flex-none text-ink-tertiary">
                  {(file.size / 1024 / 1024).toFixed(1)} MB
                </span>
                <button
                  type="button"
                  disabled={ingest.isPending}
                  aria-label={`Remove ${file.name}`}
                  onClick={() => {
                    setFile(null);
                    fileInputRef.current?.focus();
                  }}
                  className="flex h-11 w-11 flex-none items-center justify-center rounded-sm text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink disabled:opacity-45"
                >
                  <RemoveIcon />
                </button>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <label htmlFor={groupLabelId} className="text-sm font-medium text-ink">
                Group name or label
              </label>
              <input
                id={groupLabelId}
                type="text"
                value={groupLabel}
                disabled={ingest.isPending}
                maxLength={200}
                placeholder="e.g. Group 5"
                aria-describedby={groupHintId}
                onChange={(event) => setGroupLabel(event.target.value)}
                className="min-h-11 w-full rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9"
              />
              <p id={groupHintId} className="text-sm text-ink-secondary">
                {`Optional. Helps you tell manuscripts apart once you have several, for example a project or team name. If left blank, this manuscript is labeled "${UNGROUPED_LABEL}".`}
              </p>
            </div>

            {ingest.isPending && (
              <p role="status" className="flex items-center gap-2 text-sm text-ink-secondary">
                <SpinnerIcon />
                Uploading and parsing your manuscript. Longer documents can take up to a minute.
              </p>
            )}

            {message && (
              <p
                ref={errorRef}
                id={errorId}
                role="alert"
                tabIndex={-1}
                className="text-sm font-medium text-danger"
              >
                <span className="sr-only">Error: </span>
                {message}
              </p>
            )}
          </div>
        )}
      </Modal>
    </ModalBackdrop>
  );
}
