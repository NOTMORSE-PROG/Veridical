// Screen 4c — Upload & parse format (modal, F2.1). The wireframe sketches
// granular per-stage progress (extract / decompose / validate); the
// backend runs that whole pipeline as one request and can't honestly
// report intermediate stages yet, so this shows one real "parsing" state
// instead of inventing a fake step sequence (charter rule 3: never
// present unverified progress as fact).
import { useId, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { cx } from "../components/cx";
import { Modal, ModalBackdrop } from "../components/Modal";
import { useUploadRubric } from "./useRubric";

interface UploadRubricModalProps {
  onClose: () => void;
  /** Set when uploading a NEW VERSION of an existing family (screen 4m's
   * "Upload new format") — omitted for a brand-new format (screen 4b). */
  familyId?: string;
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

export function UploadRubricModal({ onClose, familyId }: UploadRubricModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const upload = useUploadRubric();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileInputId = useId();
  const hintId = useId();
  const errorId = useId();

  function handleContinue() {
    setFieldError(null);
    if (!file) {
      setFieldError("Choose a format document before continuing.");
      return;
    }
    upload.mutate(
      { file, title: file.name, familyId },
      { onSuccess: (rubric) => navigate(`/rubric/${rubric.id}/review`) },
    );
  }

  const serverError =
    upload.error instanceof ApiError
      ? upload.error.message
      : upload.error
        ? "Upload failed. Please try again."
        : null;
  const message = fieldError ?? serverError;

  return (
    <ModalBackdrop>
      <Modal
        title={familyId ? "Upload new format version" : "Upload required format"}
        onClose={upload.isPending ? undefined : onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              disabled={upload.isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleContinue}
              disabled={upload.isPending}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {upload.isPending ? "Parsing" : "Continue to review"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-4" aria-busy={upload.isPending}>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink">Format document</span>
            <p id={hintId} className="text-sm text-ink-secondary">
              PDF or DOCX.
            </p>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
              <input
                ref={fileInputRef}
                id={fileInputId}
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                disabled={upload.isPending}
                aria-describedby={message ? `${hintId} ${errorId}` : hintId}
                aria-invalid={message ? true : undefined}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  // Choosing a file is the natural "I've addressed the
                  // problem" action -- clear any stale field/server error
                  // immediately rather than leaving the trigger red until
                  // the next Continue click (matches ui-designer's spec:
                  // the "File selected" state has no error styling).
                  setFieldError(null);
                  upload.reset();
                }}
                className="file-trigger-input sr-only"
              />
              <label
                htmlFor={fileInputId}
                className={cx(
                  "inline-flex h-11 w-full items-center justify-center gap-2 rounded-md border px-4 text-sm font-bold sm:w-auto",
                  upload.isPending
                    ? "cursor-not-allowed border-border-disabled bg-panel text-ink-tertiary opacity-60"
                    : message
                      ? "cursor-pointer border-2 border-danger bg-panel text-ink hover:bg-status-neutral-bg"
                      : "cursor-pointer border-border-input bg-panel text-ink hover:bg-status-neutral-bg",
                )}
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
                disabled={upload.isPending}
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

          {/* role="status" already carries an implicit aria-live="polite" --
              a separate sr-only live region here would double-announce
              the same pending state (found live, V-055). */}
          {upload.isPending && (
            <p role="status" className="flex items-center gap-2 text-sm text-ink-secondary">
              <SpinnerIcon />
              Parsing your document. This can take a few seconds.
            </p>
          )}

          {message && (
            <p id={errorId} role="alert" className="text-sm font-medium text-danger">
              <span className="sr-only">Error: </span>
              {message}
            </p>
          )}

          <p className="rounded-md bg-status-info-bg px-3 py-2 text-sm text-status-info-text">
            If the file cannot be read, you will see why and can try a different one. Every
            successfully parsed format goes to your review before anything is confirmed or used.
          </p>
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
