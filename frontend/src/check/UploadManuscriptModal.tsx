// V-059 — the manuscript half of "uploads a required format... and a
// manuscript" (the product's own one-line description). The format half
// has had a real screen since V-012 (UploadRubricModal.tsx, screen 4c);
// this is the same shape pointed at the ingestion endpoint instead --
// same Modal base, same file-input/error pattern. No mandatory review
// screen follows this one (a manuscript has nothing to confirm the way a
// parsed rubric does), so unlike the rubric modal this one shows a real
// success state in place rather than auto-navigating away.
//
// V-063 (owner's call, 2026-08-19): this modal used to also carry its own
// free-text "Group name or label" field, resolved into a real `Group`
// immediately at ingest. UX review reproduced why that no longer belongs
// here: typing a label created a real group, which the group-proposal
// dialog below then silently reassigned the manuscript away from moments
// later, leaving the first one an orphaned, empty group with no notice to
// the instructor. Assignment happens after upload through reviewed details or
// an explicitly selected saved group; ingest starts at the
// "Ungrouped" default (`resolve_or_create_group`'s own fallback).
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { isKnownUploadRejection, isUploadFileError, StaleUploadContextError } from "../api/uploadOutcome";
import { useUploadScope } from "../auth/useUploadScope";
import { UploadAttemptNotice, UploadSessionNotice } from "../components/UploadAttemptNotice";
import { Modal, ModalBackdrop } from "../components/Modal";
import type { GroupAssignmentOutput } from "../api/groupAssignmentOutcome";
import { formatFileSize } from "../domain/fileSize";
import { Button } from "../ui/Button";
import { GroupAssignmentFields } from "./GroupAssignmentFields";
import { useIngestManuscript } from "./useCheckRun";
import { useBackgroundUploads } from "../upload/BackgroundUploads";
import { BACKGROUND_UPLOAD_MIN_BYTES } from "../config/ui";
import { useRubricFamilies } from "../rubric/useRubric";
import { formatChoiceLabel } from "../domain/formatIdentity";

function formatUploadDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

interface UploadManuscriptModalProps {
  onClose: (createdManuscriptId?: number) => void;
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
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [groupResolved, setGroupResolved] = useState(false);
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(null);
  const ingest = useIngestManuscript();
  const background = useBackgroundUploads();
  const queryClient = useQueryClient();
  const [startWhenReady, setStartWhenReady] = useState(false);
  const [startRubricId, setStartRubricId] = useState<number | "">("");
  const startCheckId = useId();
  const startFormatId = useId();
  const inBackground = Boolean(background && file && file.size >= BACKGROUND_UPLOAD_MIN_BYTES);
  const offersStartCheck = inBackground && Boolean(background?.canStartCheck);
  const { data: families, isPending: familiesPending } = useRubricFamilies({ enabled: offersStartCheck });
  const activeFamilies = useMemo(
    () => (families ?? []).filter((f) => f.is_active).sort((a, b) => a.title.localeCompare(b.title)),
    [families],
  );
  // The manuscript's program is only known once its pages are read, so every
  // active required format is offered; the only one is chosen for you.
  const chosenRubricId = activeFamilies.length === 1 ? activeFamilies[0].id : startRubricId;
  const resetIngest = ingest.reset;
  const scope = useUploadScope();
  const { status: scopeStatus, isCurrent: isScopeCurrent } = scope;
  const [unknownFiles, setUnknownFiles] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const sending = useRef(false);
  const recoveringFocus = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const uploadAnotherRef = useRef<HTMLButtonElement>(null);
  const startCheckRef = useRef<HTMLButtonElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  const fileInputId = useId();
  const hintId = useId();
  const errorId = useId();
  const lastCreatedManuscriptId = useRef<number | undefined>(undefined);

  function checkSession() {
    recoveringFocus.current = true;
    void scope.verify().catch(() => {});
  }
  useEffect(() => {
    if (scopeStatus !== "ready" || !recoveringFocus.current || !isScopeCurrent()) return;
    recoveringFocus.current = false;
    if (document.activeElement === document.body) fileInputRef.current?.focus();
  }, [scopeStatus, isScopeCurrent]);

  // Same BUG-028 rationale as UploadRubricModal: a focused element that
  // becomes `disabled` mid-pending drops focus to <body>. Moving focus to
  // the error message on failure is the recovery path, not a proactive
  // guard (this modal has no field that's ever focused-then-disabled on
  // the error path itself).
  useEffect(() => {
    if (ingest.isError && scopeStatus === "ready" && isScopeCurrent()) errorRef.current?.focus();
  }, [ingest.isError, scopeStatus, isScopeCurrent]);

  // BUG-234/UX review: the asynchronous form-to-result replacement used to
  // unmount the focused Upload button and strand focus on <body>. A visible
  // result heading gives keyboard and screen-reader users the same immediate
  // feedback sighted pointer users receive.
  useEffect(() => {
    if (ingest.data && scopeStatus === "ready" && isScopeCurrent()) {
      resultHeadingRef.current?.focus();
      // Duplicate notices describe an earlier copy; ingestion still creates
      // a new unchecked manuscript that must remain discoverable on close.
      lastCreatedManuscriptId.current = ingest.data.manuscript_id;
    }
  }, [ingest.data, scopeStatus, isScopeCurrent]);

  useEffect(() => {
    if (scopeStatus !== "lost") return;
    setFile(null);
    setUnknownFiles([]);
    lastCreatedManuscriptId.current = undefined;
    resetIngest();
  }, [scopeStatus, resetIngest]);

  useEffect(() => {
    if (unknownFiles.length === 0 || scopeStatus !== "ready" || !isScopeCurrent()) return;
    void queryClient.invalidateQueries({ queryKey: ["manuscripts"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  }, [unknownFiles.length, scopeStatus, isScopeCurrent, queryClient]);

  function handleClose() {
    onClose(isScopeCurrent() ? lastCreatedManuscriptId.current : undefined);
  }

  function handleUpload(confirmed = false) {
    setFieldError(null);
    if (!file) {
      setFieldError("Choose a manuscript file before uploading.");
      return;
    }
    if (sending.current || scopeStatus !== "ready" || !isScopeCurrent()) return;
    if (unknownFiles.length > 0 && !confirmed) { setConfirming(true); return; }
    if (background && file.size >= BACKGROUND_UPLOAD_MIN_BYTES) {
      if (offersStartCheck && startWhenReady && chosenRubricId === "") {
        setFieldError("Choose the required format for the check, or untick Start a check when it's ready.");
        return;
      }
      setConfirming(false);
      background.start(file, offersStartCheck && startWhenReady && chosenRubricId !== "" ? { startCheckRubricId: chosenRubricId } : {});
      onClose();
      return;
    }
    const attempt = scope.capture();
    const submittedFile = file;
    setConfirming(false);
    sending.current = true;
    ingest.mutate({ file: submittedFile, scope: attempt }, {
      onError(error) {
        if (attempt.isCurrent() && !(error instanceof StaleUploadContextError)
          && !isKnownUploadRejection(error, "manuscript")) {
          setUnknownFiles((previous) => [...previous, submittedFile.name]);
        }
      },
      onSettled() { sending.current = false; },
    });
  }

  function handleUploadAnother() {
    setFile(null);
    ingest.reset();
    setGroupResolved(false);
    fileInputRef.current?.focus();
  }

  const serverError =
    ingest.error instanceof ApiError
      ? ingest.error.message
      : ingest.error
        ? "VERIDICAL could not confirm this upload's result. Review your uploads before starting another attempt."
        : null;
  const message = fieldError ?? serverError;
  const fileError = Boolean(fieldError) || isUploadFileError(ingest.error);
  const summary = ingest.data;

  const extractionNotes = summary?.notes.filter((note) => note.trim()) ?? [];
  const showsVisionUnavailable =
    summary?.vision_status === "unavailable" &&
    summary.images + summary.tables + summary.equations > 0;

  // Assignment remains available even when no identity was extracted.
  const [assignmentBusy, setAssignmentBusy] = useState(false);
  const showGroupProposal = summary !== undefined && !groupResolved;

  function handleGroupProposalDone(_result: GroupAssignmentOutput | null) {
    setGroupResolved(true);
  }

  // UX review (V-063 review): the group-proposal form unmounts the
  // instant it resolves (Skip/Done), and nothing claimed focus on the
  // way back to the plain footer -- same BUG-028/`removeMember` rule as
  // everywhere else this codebase enforces it: a control that unmounts
  // mid-interaction must hand focus somewhere real.
  useEffect(() => {
    if (!groupResolved || scopeStatus !== "ready" || !isScopeCurrent()) return;
    if (summary?.existing_upload) startCheckRef.current?.focus();
    else uploadAnotherRef.current?.focus();
  }, [groupResolved, summary?.existing_upload, scopeStatus, isScopeCurrent]);

  if (scopeStatus === "lost") return <ModalBackdrop><Modal title="Upload manuscript" onClose={handleClose}>
    <UploadSessionNotice status={scopeStatus} onClose={handleClose} onCheck={checkSession} />
  </Modal></ModalBackdrop>;

  return (
    <ModalBackdrop>
      <Modal
        title="Upload manuscript"
        size="lg"
        className="signal-upload-sheet"
        onClose={ingest.isPending || assignmentBusy ? undefined : handleClose}
        footer={
          scopeStatus !== "ready" ? null : summary ? (
            showGroupProposal ? <div ref={setActionsTarget} /> : (
              <>
                <button
                  ref={uploadAnotherRef}
                  type="button"
                  onClick={handleUploadAnother}
                  className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
                >
                  Upload another
                </button>
                <button
                  ref={startCheckRef}
                  type="button"
                  onClick={() => { void scope.verify().then(() => {
                    if (isScopeCurrent()) onUploadSuccess(summary.manuscript_id);
                  }).catch(() => {}); }}
                  className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
                >
                  Start a check with this manuscript
                </button>
              </>
            )
          ) : (
            <>
              <button
                type="button"
                onClick={handleClose}
                disabled={ingest.isPending}
                className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
              >
                {unknownFiles.length > 0 ? "Close form" : "Cancel"}
              </button>
              <button
                ref={submitRef}
                type="button"
                onClick={() => handleUpload()}
                disabled={ingest.isPending}
                aria-busy={ingest.isPending ? "true" : undefined}
                className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
              >
                {ingest.isPending ? "Uploading." : unknownFiles.length > 0 ? "Start another upload attempt" : "Upload manuscript"}
              </button>
            </>
          )
        }
      >
        {scopeStatus !== "ready" && <UploadSessionNotice status={scopeStatus} onClose={handleClose}
          onCheck={checkSession} />}
        <fieldset disabled={scopeStatus !== "ready"} hidden={scopeStatus !== "ready"}
          inert={scopeStatus !== "ready"} className={scopeStatus !== "ready" ? "hidden" : "contents"}>
        <UploadAttemptNotice kind="manuscript" filenames={unknownFiles} selectedFilename={file?.name} confirming={confirming}
          onConfirm={() => handleUpload(true)} onCancel={() => { setConfirming(false); submitRef.current?.focus(); }} />
        {summary ? (
          <div className="signal-group-flow signal-upload-summary">
            <h3 ref={resultHeadingRef} tabIndex={-1} className="text-base font-bold text-ink">
              {summary.existing_upload ? "You uploaded this file before" : "Upload complete"}
            </h3>
            <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
              {summary.existing_upload
                ? `Upload complete. This is the same file you uploaded on ${formatUploadDate(summary.existing_upload.created_at)}.`
                : "Upload complete."}
            </p>
            {summary.existing_upload && (
              <section className="signal-group-flow rounded-md border border-border bg-status-neutral-bg px-3 py-3 text-sm text-ink">
                <p className="text-ink-secondary">
                  This is the exact same file you uploaded on {formatUploadDate(summary.existing_upload.created_at)}.
                  {summary.existing_upload.original_filename
                    && summary.existing_upload.original_filename !== file?.name
                    && ` That upload was named “${summary.existing_upload.original_filename}”.`}
                  {" "}This new copy was saved and can be checked on its own.
                </p>
                {summary.existing_upload.purged_at ? (
                  <p className="text-ink-secondary">The earlier copy's stored file was removed.</p>
                ) : null}
                {showGroupProposal && (
                  <div className="flex flex-col items-start gap-2 sm:flex-row">
                      <Button type="button" variant="secondary" disabled={assignmentBusy} onClick={() => setGroupResolved(true)}>
                      Skip group details and continue
                    </Button>
                  </div>
                )}
              </section>
            )}
            <p className="signal-upload-summary__counts">
              {summary.anchor_kind === "paragraph"
                ? "Word document processed using paragraph locations. Page count is not available."
                : summary.anchor_kind === "page"
                  ? summary.page_count > 0
                    ? `${summary.page_count} ${summary.page_count === 1 ? "page" : "pages"} parsed.`
                    : "The parser reported 0 pages for this PDF. Check the file before starting a check."
                  : "Page count is not available for this upload."}
              {" "}{summary.citations} reference {summary.citations === 1 ? "record" : "records"} extracted.
            </p>
            {(extractionNotes.length > 0 || showsVisionUnavailable) && (
              <section className="signal-upload-summary__notes" aria-label="Extraction notes">
                <h4>Extraction notes</h4>
                {extractionNotes.map((note, index) => (
                  <p key={index} className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
                    {note}
                  </p>
                ))}
                {showsVisionUnavailable && (
                  <p className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
                    VERIDICAL could not analyze the images in this document right now (the image
                    reading model is unavailable). Checks that depend on image content may be
                    limited.
                  </p>
                )}
              </section>
            )}
            {showGroupProposal && (
              <GroupAssignmentFields
                manuscriptId={summary.manuscript_id}
                initialProposal={summary.group_proposal}
                onBusyChange={setAssignmentBusy}
                onDone={handleGroupProposalDone}
                actionsTarget={actionsTarget}
              />
            )}
          </div>
        ) : (
          <div className="signal-group-flow" aria-busy={ingest.isPending}>
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink">Manuscript file</span>
              <p id={hintId} className="text-sm text-ink-secondary">
                PDF or DOCX. Checked manuscripts are compared against the Library and are not added to it. To
                add a finished manuscript to the Library, open Library and choose Add to Library.
              </p>

              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <input
                  ref={fileInputRef}
                  id={fileInputId}
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  disabled={ingest.isPending}
                  aria-describedby={fileError ? `${hintId} ${errorId}` : hintId}
                  aria-invalid={fileError ? true : undefined}
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null);
                    setFieldError(null);
                    setConfirming(false);
                    ingest.reset();
                  }}
                  className="file-trigger-input sr-only"
                />
                <label
                  htmlFor={fileInputId}
                  className={
                    ingest.isPending
                      ? "inline-flex h-11 w-full cursor-not-allowed items-center justify-center gap-2 rounded-md border border-border-disabled bg-panel px-4 text-sm font-bold text-ink-tertiary opacity-60 sm:w-auto"
                      : fileError
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
                  {formatFileSize(file.size)}
                </span>
                <button
                  type="button"
                  disabled={ingest.isPending}
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

            {inBackground && (
              <section className="signal-upload-background" aria-labelledby={`${startCheckId}-title`}>
                <h3 id={`${startCheckId}-title`}>This file uploads in the background</h3>
                <p>It is large, so this dialog closes and an Uploads panel in the lower right shows its progress. Keep this tab open until the file is sent; after that you can close it, and the server keeps preparing the file.</p>
                {offersStartCheck && (familiesPending ? (
                  <p>Loading your required formats.</p>
                ) : activeFamilies.length === 0 ? (
                  <p>No required format is active yet, so start the check from the Uploads panel once the file is ready.</p>
                ) : (
                  <>
                    <label className="signal-upload-background__toggle">
                      <input id={startCheckId} type="checkbox" checked={startWhenReady}
                        onChange={(event) => { setStartWhenReady(event.target.checked); setFieldError(null); }} />
                      <span>Start a check when it's ready</span>
                    </label>
                    {!startWhenReady && <p>Left unticked, the manuscript waits under Not checked on the Review Desk until you start its check.</p>}
                    {startWhenReady && (activeFamilies.length === 1 ? (
                      <p>The check uses {formatChoiceLabel(activeFamilies[0], activeFamilies)}.</p>
                    ) : (
                      <div className="signal-upload-background__format">
                        <label htmlFor={startFormatId}>Required format for the check (required)</label>
                        <p id={`${startFormatId}-hint`} className="signal-field-hint">The check grades the manuscript against this format.</p>
                        <select id={startFormatId} value={startRubricId} required aria-required="true"
                          aria-describedby={`${startFormatId}-hint${fieldError && startRubricId === "" ? ` ${errorId}` : ""}`}
                          aria-invalid={fieldError && startRubricId === "" ? true : undefined}
                          onChange={(event) => { setStartRubricId(event.target.value === "" ? "" : Number(event.target.value)); setFieldError(null); }}
                          className="min-h-11 w-full rounded-md border border-border-input bg-panel px-3 text-base text-ink">
                          <option value="">Select a required format.</option>
                          {activeFamilies.map((f) => <option key={f.id} value={f.id}>{formatChoiceLabel(f, activeFamilies)}</option>)}
                        </select>
                      </div>
                    ))}
                  </>
                ))}
              </section>
            )}

            {ingest.isPending && (
              <p role="status" className="flex items-center gap-2 text-sm text-ink-secondary">
                <SpinnerIcon />
                Uploading your file and preparing the manuscript.
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
        </fieldset>
      </Modal>
    </ModalBackdrop>
  );
}
