// Screen 4c — Upload & parse format (modal, F2.1). The wireframe sketches
// granular per-stage progress (extract / decompose / validate); the
// backend runs that whole pipeline as one request and can't honestly
// report intermediate stages yet, so this shows one real "parsing" state
// instead of inventing a fake step sequence (charter rule 3: never
// present unverified progress as fact).
import { useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { useUploadRubric } from "./useRubric";

interface UploadRubricModalProps {
  onClose: () => void;
  /** Set when uploading a NEW VERSION of an existing family (screen 4m's
   * "Upload new format") — omitted for a brand-new format (screen 4b). */
  familyId?: string;
}

export function UploadRubricModal({ onClose, familyId }: UploadRubricModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const upload = useUploadRubric();
  const navigate = useNavigate();

  function handleContinue() {
    setFieldError(null);
    if (!file) {
      setFieldError("Choose a PDF or DOCX file first.");
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
              className="rounded-control border border-border-button bg-panel px-3.5 py-1.5 text-base font-medium text-ink disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleContinue}
              disabled={upload.isPending}
              className="rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary disabled:opacity-45"
            >
              {upload.isPending ? "Parsing…" : "Continue to review"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
              Format document (PDF or DOCX)
            </span>
            <input
              type="file"
              accept=".pdf,.docx"
              disabled={upload.isPending}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="text-xs"
            />
          </label>
          {file && (
            <div className="flex items-center gap-2 rounded-panel border border-border bg-page px-3 py-2 text-xs">
              <b>{file.name}</b>
              <span className="text-ink-faint">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
            </div>
          )}
          {upload.isPending && (
            <p className="text-xs text-ink-soft">
              Parsing your document — this can take a few seconds…
            </p>
          )}
          {message && (
            <p role="alert" className="text-xs font-medium text-status-bad-text">
              {message}
            </p>
          )}
          <p className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text">
            A malformed parse retries automatically. If every attempt fails, the raw parse opens
            for manual correction — nothing activates without your confirmation.
          </p>
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
