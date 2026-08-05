// Screen 4f — New manuscript check (modal, F3.1-F3.6). Manuscripts are
// uploaded/ingested separately (V-008); this modal only picks WHICH
// already-ingested manuscript to run against WHICH active rubric.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { useRubricFamilies } from "../rubric/useRubric";
import { useCreateCheckRun, useManuscripts } from "./useCheckRun";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

interface Problem {
  id: string;
  message: string;
  focus: () => void;
}

export function NewCheckModal({ onClose }: { onClose: () => void }) {
  const {
    data: manuscripts,
    isPending: manuscriptsPending,
    isError: manuscriptsError,
    refetch: refetchManuscripts,
  } = useManuscripts();
  const {
    data: families,
    isPending: familiesPending,
    isError: familiesError,
    refetch: refetchFamilies,
  } = useRubricFamilies();
  const create = useCreateCheckRun();
  const navigate = useNavigate();

  const readyManuscripts = useMemo(
    () => (manuscripts ?? []).filter((m) => m.ingest_status === "done"),
    [manuscripts],
  );
  const pendingManuscripts = useMemo(
    () => (manuscripts ?? []).filter((m) => m.ingest_status === "pending" || m.ingest_status === "processing"),
    [manuscripts],
  );
  const activeFamilies = useMemo(
    () => (families ?? []).filter((f) => f.is_active).sort((a, b) => a.title.localeCompare(b.title)),
    [families],
  );

  const [manuscriptId, setManuscriptId] = useState<number | "">("");
  const [chosenRubricId, setChosenRubricId] = useState<number | "">("");
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [focusSummaryToken, setFocusSummaryToken] = useState(0);

  const manuscriptSelectRef = useRef<HTMLSelectElement>(null);
  const rubricSelectRef = useRef<HTMLSelectElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focusSummaryToken === 0) return;
    summaryRef.current?.focus();
  }, [focusSummaryToken]);

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
  const selectedManuscript = readyManuscripts.find((m) => m.id === manuscriptId);
  const selectedRubric = activeFamilies.find((f) => f.id === rubricId);

  function computeProblems(): Problem[] {
    const problems: Problem[] = [];
    if (readyManuscripts.length === 0) {
      problems.push({ id: "no-manuscripts", message: "No manuscripts are available to check yet.", focus: () => {} });
    } else if (manuscriptId === "") {
      problems.push({ id: "manuscript", message: "Choose a manuscript.", focus: () => manuscriptSelectRef.current?.focus() });
    }
    if (activeFamilies.length === 0) {
      problems.push({ id: "no-rubric", message: "No active rubric is available yet.", focus: () => {} });
    } else if (activeFamilies.length > 1 && rubricId === "") {
      problems.push({
        id: "rubric",
        message: "Choose which active rubric to check against.",
        focus: () => rubricSelectRef.current?.focus(),
      });
    }
    return problems;
  }

  function handleStart() {
    setSubmitAttempted(true);
    const problems = computeProblems();
    if (problems.length > 0) {
      setFocusSummaryToken((t) => t + 1);
      return;
    }
    create.mutate(
      { manuscript_id: manuscriptId as number, rubric_id: rubricId as number },
      { onSuccess: (run) => navigate(`/checks/${run.id}`) },
    );
  }

  const currentProblems = submitAttempted ? computeProblems() : [];
  const manuscriptInvalid = currentProblems.some((p) => p.id === "manuscript");
  const rubricInvalid = currentProblems.some((p) => p.id === "rubric");
  const serverError =
    create.error instanceof ApiError
      ? create.error.message
      : create.error
        ? "Could not start the check. Please try again."
        : null;

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
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleStart}
              disabled={create.isPending}
              aria-busy={create.isPending ? "true" : undefined}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
            >
              {create.isPending ? "Starting check." : "Start check"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {currentProblems.length > 0 && (
            <div
              ref={summaryRef}
              tabIndex={-1}
              role="alert"
              className="rounded-md border-2 border-status-attention-text/40 bg-status-attention-bg p-3.5 text-sm text-status-attention-text outline-none"
            >
              <p className="font-semibold">Fix the following before starting this check:</p>
              <ul className="mt-1.5 list-disc pl-5">
                {currentProblems.map((p) => (
                  <li key={p.id}>
                    {p.focus ? (
                      <button type="button" onClick={p.focus} className="text-link underline hover:text-link-hover">
                        {p.message}
                      </button>
                    ) : (
                      p.message
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* A plain div, not a <label> wrapping the whole field: <button>
              is an HTML-labelable element, so a <label> that also
              contains the "Try again" retry button (error state) would
              fold the label's own text into that button's computed
              accessible name (confirmed live in dom-accessibility-api —
              the button's name became "Manuscript Could not load your
              manuscripts. ." instead of "Try again"). aria-labelledby on
              the select alone avoids this while still naming it. */}
          <div className="flex flex-col gap-1.5">
            <span id="manuscript-label" className="text-sm font-medium text-ink">
              Manuscript
            </span>
            {manuscriptsPending ? (
              <div role="status" aria-live="polite" aria-busy="true" className="flex h-9 items-center gap-2 rounded-md border border-border bg-page px-3 text-sm text-ink-secondary">
                <SpinnerIcon />
                Loading manuscripts.
              </div>
            ) : manuscriptsError ? (
              <div role="alert" className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text">
                Could not load your manuscripts.{" "}
                <button type="button" onClick={() => refetchManuscripts()} className="font-medium underline">
                  Try again
                </button>
                .
              </div>
            ) : readyManuscripts.length === 0 ? (
              <p className="rounded-md border border-border bg-page px-3 py-2.5 text-sm text-ink-secondary">
                {manuscripts && manuscripts.length === 0
                  ? "No manuscripts have been ingested yet. Once one finishes processing, it will appear here."
                  : pendingManuscripts.length > 0
                    ? "Your manuscripts are still being processed. This can take a few minutes. Check back shortly."
                    : "None of your ingested manuscripts finished successfully. Close this and check the dashboard for why each one failed."}
              </p>
            ) : (
              <>
                <select
                  ref={manuscriptSelectRef}
                  aria-labelledby="manuscript-label"
                  value={manuscriptId}
                  onChange={(event) =>
                    setManuscriptId(event.target.value === "" ? "" : Number(event.target.value))
                  }
                  aria-invalid={manuscriptInvalid ? "true" : undefined}
                  aria-describedby={manuscriptInvalid ? "manuscript-err" : undefined}
                  className={`min-h-11 w-full rounded-md border bg-panel px-3 text-base text-ink sm:h-9 ${manuscriptInvalid ? "border-2 border-status-attention-text" : "border-border-input"}`}
                >
                  <option value="">Select a manuscript.</option>
                  {readyManuscripts.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.group_label}, uploaded {dateFormatter.format(new Date(m.created_at))}
                    </option>
                  ))}
                </select>
                {manuscriptInvalid && (
                  <p id="manuscript-err" className="text-sm text-status-attention-text">
                    Choose a manuscript.
                  </p>
                )}
                {selectedManuscript && (
                  <p className="text-sm text-ink-secondary">
                    Selected: <b className="font-medium text-ink">{selectedManuscript.group_label}</b>, uploaded{" "}
                    {dateFormatter.format(new Date(selectedManuscript.created_at))}.
                  </p>
                )}
              </>
            )}
          </div>

          {activeFamilies.length > 1 && (
            <div className="flex flex-col gap-1.5">
              <span id="rubric-label" className="text-sm font-medium text-ink">
                Rubric
              </span>
              {familiesPending ? (
                <div role="status" aria-live="polite" aria-busy="true" className="flex h-9 items-center gap-2 rounded-md border border-border bg-page px-3 text-sm text-ink-secondary">
                  <SpinnerIcon />
                  Loading rubrics.
                </div>
              ) : familiesError ? (
                <div role="alert" className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text">
                  Could not load your rubrics.{" "}
                  <button type="button" onClick={() => refetchFamilies()} className="font-medium underline">
                    Try again
                  </button>
                  .
                </div>
              ) : (
                <>
                  <select
                    ref={rubricSelectRef}
                    aria-labelledby="rubric-label"
                    value={rubricId}
                    onChange={(event) =>
                      setChosenRubricId(event.target.value === "" ? "" : Number(event.target.value))
                    }
                    aria-invalid={rubricInvalid ? "true" : undefined}
                    aria-describedby={rubricInvalid ? "rubric-err" : undefined}
                    className={`min-h-11 w-full rounded-md border bg-panel px-3 text-base text-ink sm:h-9 ${rubricInvalid ? "border-2 border-status-attention-text" : "border-border-input"}`}
                  >
                    <option value="">Select an active rubric.</option>
                    {activeFamilies.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.title} (v{f.version})
                      </option>
                    ))}
                  </select>
                  {rubricInvalid && (
                    <p id="rubric-err" className="text-sm text-status-attention-text">
                      Choose which active rubric to check against.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {!familiesPending && !familiesError && activeFamilies.length === 0 && (
            <p className="rounded-md border border-border bg-page px-3 py-2.5 text-sm text-ink-secondary">
              No active rubric yet. Confirm one on the rubric review screen first.
            </p>
          )}

          {selectedRubric && (
            // Wraps rather than truncates (unlike Chip.tsx's per-row table
            // cells, this box has the modal's full width to work with) —
            // a `title` tooltip is mouse-hover-only and unreachable by
            // keyboard/touch, so the safer fix is not hiding anything in
            // the first place (ux-critic finding, V-055 4f review).
            <div className="rounded-md bg-status-info-bg px-3 py-2 text-sm break-words text-status-info-text">
              Runs against <b>{selectedRubric.title}</b> v{selectedRubric.version},{" "}
              {selectedRubric.criteria_count} criteria.
            </div>
          )}

          <p className="text-sm text-ink-tertiary">
            Checks run one at a time. If another is already in progress, this one queues automatically and starts
            as soon as it's free.
          </p>

          {serverError && (
            <p role="alert" className="text-sm font-medium text-status-attention-text">
              <span className="sr-only">Error: </span>
              {serverError}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
