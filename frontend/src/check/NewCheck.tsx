// Screen 4f — New manuscript check (modal, F3.1-F3.6). Manuscripts are
// uploaded/ingested separately (V-008); this modal only picks WHICH
// already-ingested manuscript to run against WHICH active rubric.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { useRubricFamilies } from "../rubric/useRubric";
import { useCreateCheckRun, useManuscripts } from "./useCheckRun";

export function NewCheckModal({ onClose }: { onClose: () => void }) {
  const { data: manuscripts } = useManuscripts();
  const { data: families } = useRubricFamilies();
  const create = useCreateCheckRun();
  const navigate = useNavigate();

  const readyManuscripts = useMemo(
    () => (manuscripts ?? []).filter((m) => m.ingest_status === "done"),
    [manuscripts],
  );
  const activeFamilies = useMemo(() => (families ?? []).filter((f) => f.is_active), [families]);

  const [manuscriptId, setManuscriptId] = useState<number | "">("");
  const [chosenRubricId, setChosenRubricId] = useState<number | "">("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  // Auto-selects the sole active rubric once it loads (most instructors
  // have exactly one); an explicit pick always wins once made. Derived
  // per render rather than synced via an effect — `families` arrives
  // asynchronously, so a one-time useState initializer would miss it.
  const rubricId: number | "" =
    chosenRubricId !== ""
      ? chosenRubricId
      : activeFamilies.length === 1
        ? activeFamilies[0].id
        : "";
  const selectedRubric = activeFamilies.find((f) => f.id === rubricId);

  function handleStart() {
    setFieldError(null);
    if (manuscriptId === "") {
      setFieldError("Choose a manuscript first.");
      return;
    }
    if (rubricId === "") {
      setFieldError("Choose which active rubric version to check against.");
      return;
    }
    create.mutate(
      { manuscript_id: manuscriptId, rubric_id: rubricId },
      { onSuccess: (run) => navigate(`/checks/${run.id}`) },
    );
  }

  const serverError =
    create.error instanceof ApiError
      ? create.error.message
      : create.error
        ? "Could not start the check. Please try again."
        : null;
  const message = fieldError ?? serverError;

  return (
    <ModalBackdrop>
      <Modal
        title="New manuscript check"
        onClose={create.isPending ? undefined : onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              disabled={create.isPending}
              className="rounded-control border border-border-button bg-panel px-3.5 py-1.5 text-base font-medium text-ink disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleStart}
              disabled={create.isPending || readyManuscripts.length === 0 || activeFamilies.length === 0}
              className="rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary disabled:opacity-45"
            >
              {create.isPending ? "Starting…" : "Start check"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
              Manuscript
            </span>
            <select
              value={manuscriptId}
              onChange={(event) =>
                setManuscriptId(event.target.value === "" ? "" : Number(event.target.value))
              }
              className="rounded-control border border-border-button bg-panel px-2.5 py-1.5 text-xs"
            >
              <option value="">Select a manuscript…</option>
              {readyManuscripts.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.group_label}
                </option>
              ))}
            </select>
          </label>

          {activeFamilies.length > 1 && (
            <label className="flex flex-col gap-1">
              <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
                Rubric
              </span>
              <select
                value={rubricId}
                onChange={(event) =>
                  setChosenRubricId(event.target.value === "" ? "" : Number(event.target.value))
                }
                className="rounded-control border border-border-button bg-panel px-2.5 py-1.5 text-xs"
              >
                <option value="">Select an active rubric…</option>
                {activeFamilies.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.title} (v{f.version})
                  </option>
                ))}
              </select>
            </label>
          )}

          {selectedRubric && (
            <div className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text">
              Runs against <b>{selectedRubric.title}</b> v{selectedRubric.version} ·{" "}
              {selectedRubric.criteria_count} criteria
            </div>
          )}
          {activeFamilies.length === 0 && (
            <p className="text-xs text-ink-faint">
              No active rubric yet — confirm one on the rubric review screen first.
            </p>
          )}
          {readyManuscripts.length === 0 && (
            <p className="text-xs text-ink-faint">No ingested manuscripts available yet.</p>
          )}
          <p className="pnote text-xs text-ink-faint">
            Runs queue — you can start another upload while this one is processing.
          </p>
          {message && (
            <p role="alert" className="text-xs font-medium text-status-bad-text">
              {message}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
