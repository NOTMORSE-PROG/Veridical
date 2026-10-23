// Screen 4c — Upload & parse format (modal, F2.1). The wireframe sketches
// granular per-stage progress (extract / decompose / validate); the
// backend runs that whole pipeline as one request and can't honestly
// report intermediate stages yet, so this shows one real "parsing" state
// instead of inventing a fake step sequence (charter rule 3: never
// present unverified progress as fact).
import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { isKnownUploadRejection, isUploadFileError, StaleUploadContextError } from "../api/uploadOutcome";
import { useUploadScope } from "../auth/useUploadScope";
import { UploadAttemptNotice, UploadSessionNotice } from "../components/UploadAttemptNotice";
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
  const queryClient = useQueryClient();
  const resetUpload = upload.reset;
  const scope = useUploadScope(familyId);
  const { status: scopeStatus, isCurrent: isScopeCurrent } = scope;
  const [unknownFiles, setUnknownFiles] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const sending = useRef(false);
  const recoveringFocus = useRef(false);
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const receiptHeadingRef = useRef<HTMLHeadingElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  const fileInputId = useId();
  const hintId = useId();
  const errorId = useId();

  function checkSession() {
    recoveringFocus.current = true;
    void scope.verify().catch(() => {});
  }
  useEffect(() => {
    if (scopeStatus !== "ready" || !recoveringFocus.current || !isScopeCurrent()) return;
    recoveringFocus.current = false;
    if (document.activeElement === document.body) fileInputRef.current?.focus();
  }, [scopeStatus, isScopeCurrent]);

  // BUG-028: "Continue to review" is `disabled` while `upload.isPending`,
  // which drops focus to <body> per standard browser behavior for a
  // focused element that becomes disabled — a keyboard user then has to
  // re-Tab from the top of the whole document to get back into the
  // still-open dialog. The field-validation path (missing file) never
  // disables the button, so it never loses focus and doesn't need this.
  useEffect(() => {
    if (upload.isError && scopeStatus === "ready" && isScopeCurrent()) errorRef.current?.focus();
  }, [upload.isError, scopeStatus, isScopeCurrent]);

  useEffect(() => {
    if (upload.data && scopeStatus === "ready" && isScopeCurrent()) receiptHeadingRef.current?.focus();
  }, [upload.data, scopeStatus, isScopeCurrent]);

  useEffect(() => {
    if (scopeStatus !== "lost") return;
    setFile(null);
    setUnknownFiles([]);
    resetUpload();
  }, [scopeStatus, resetUpload]);

  useEffect(() => {
    if (unknownFiles.length === 0 || scopeStatus !== "ready" || !isScopeCurrent()) return;
    void queryClient.invalidateQueries({ queryKey: ["rubric-families"] });
    void queryClient.invalidateQueries({ queryKey: ["rubric-versions"] });
    void queryClient.invalidateQueries({ queryKey: ["rubric"] });
  }, [unknownFiles.length, scopeStatus, isScopeCurrent, queryClient]);

  function handleContinue(confirmed = false) {
    setFieldError(null);
    if (!file) {
      setFieldError("Choose a format document before continuing.");
      return;
    }
    if (sending.current || scopeStatus !== "ready" || !isScopeCurrent()) return;
    if (unknownFiles.length > 0 && !confirmed) { setConfirming(true); return; }
    const attempt = scope.capture();
    const submittedFile = file;
    const hadUnknown = unknownFiles.length > 0;
    setConfirming(false);
    sending.current = true;
    upload.mutate({ file: submittedFile, title: submittedFile.name, familyId, scope: attempt }, {
      onSuccess(rubric) { if (attempt.isCurrent() && !hadUnknown) navigate(`/rubric/${rubric.id}/review`); },
      onError(error) {
        if (attempt.isCurrent() && !(error instanceof StaleUploadContextError)
          && !isKnownUploadRejection(error, "format")) {
          setUnknownFiles((previous) => [...previous, submittedFile.name]);
        }
      },
      onSettled() { sending.current = false; },
    });
  }

  const serverError =
    upload.error instanceof ApiError
      ? upload.error.message
      : upload.error
        ? "VERIDICAL could not confirm this upload's result. Review your format records before starting another attempt."
        : null;
  const message = fieldError ?? serverError;
  const fileError = Boolean(fieldError) || isUploadFileError(upload.error);

  if (scopeStatus === "lost") return <ModalBackdrop><Modal title="Upload required format" onClose={onClose}>
    <UploadSessionNotice status={scopeStatus} onClose={onClose} onCheck={checkSession} />
  </Modal></ModalBackdrop>;

  if (upload.data && unknownFiles.length > 0) return <ModalBackdrop><Modal title="Upload required format" onClose={onClose}>
    {scopeStatus !== "ready" && <UploadSessionNotice status={scopeStatus} onClose={onClose}
      onCheck={checkSession} />}
    <fieldset disabled={scopeStatus !== "ready"} hidden={scopeStatus !== "ready"}
      inert={scopeStatus !== "ready"} className={scopeStatus !== "ready" ? "hidden" : "contents"}>
    <div className="signal-group-flow">
      <h3 ref={receiptHeadingRef} tabIndex={-1} className="font-bold text-ink">Latest draft confirmed</h3>
      <p>{upload.data.title}, version {upload.data.version}.</p>
      <p>One or more earlier upload results remain unconfirmed. This receipt confirms only the latest attempt.</p>
      <p>Reviewing this draft closes this upload form. Earlier results remain unconfirmed; the selected file and local attempt notices will not be retained after you leave.</p>
      <button type="button" className="font-bold underline" onClick={() => { void scope.verify().then(() => {
        if (isScopeCurrent()) navigate(`/rubric/${upload.data.id}/review`);
      }).catch(() => {}); }}>Review latest draft</button>
      <button type="button" className="font-bold underline" onClick={onClose}>Close form</button>
    </div></fieldset>
  </Modal></ModalBackdrop>;

  return (
    <ModalBackdrop>
      <Modal
        title={familyId ? "Upload new format version" : "Upload required format"}
        onClose={upload.isPending ? undefined : onClose}
        footer={
          scopeStatus !== "ready" ? null : <>
            <button
              type="button"
              onClick={onClose}
              disabled={upload.isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              {unknownFiles.length > 0 ? "Close form" : "Cancel"}
            </button>
            <button
              ref={submitRef}
              type="button"
              onClick={() => handleContinue()}
              disabled={upload.isPending}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {upload.isPending ? "Parsing" : unknownFiles.length > 0 ? "Start another upload attempt" : "Continue to review"}
            </button>
          </>
        }
      >
        {scopeStatus !== "ready" && <UploadSessionNotice status={scopeStatus} onClose={onClose}
          onCheck={checkSession} />}
        <fieldset disabled={scopeStatus !== "ready"} hidden={scopeStatus !== "ready"}
          inert={scopeStatus !== "ready"} className={scopeStatus !== "ready" ? "hidden" : "contents"}>
        <UploadAttemptNotice kind="format" filenames={unknownFiles} selectedFilename={file?.name} familyId={familyId} confirming={confirming}
          onConfirm={() => handleContinue(true)} onCancel={() => { setConfirming(false); submitRef.current?.focus(); }} />
        <div className="signal-group-flow" aria-busy={upload.isPending}>
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
                aria-describedby={fileError ? `${hintId} ${errorId}` : hintId}
                aria-invalid={fileError ? true : undefined}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  // Choosing a file is the natural "I've addressed the
                  // problem" action -- clear any stale field/server error
                  // immediately rather than leaving the trigger red until
                  // the next Continue click (matches design review's spec:
                  // the "File selected" state has no error styling).
                  setFieldError(null);
                  setConfirming(false);
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
                    : fileError
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
                  setConfirming(false);
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

          <p className="rounded-md bg-status-info-bg px-3 py-2 text-sm text-status-info-text">
            If the file cannot be read, you will see why and can try a different one. Every
            successfully parsed format goes to your review before anything is confirmed or used.
          </p>
        </div>
        </fieldset>
      </Modal>
    </ModalBackdrop>
  );
}
