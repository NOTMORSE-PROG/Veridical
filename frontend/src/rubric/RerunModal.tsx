// V-041 (Flow E, screen 4n) — re-run manuscripts against a newly
// activated rubric version. Bulk multi-select + sequential submission
// with real per-item status (never a fake aggregate percentage, charter
// rule 3) -- ui-designer spec, 2026-08-14.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useCreateCheckRun, useManuscripts, useRerunEstimate } from "../check/useCheckRun";
import { Modal, ModalBackdrop } from "../components/Modal";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { RUNNING_STATUSES, manuscriptStatus } from "../domain/manuscriptStatus";
import { isRubricEligibleForProgram } from "../domain/rubricEligibility";
import { useRubricFamilies } from "./useRubric";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

type RowState =
  | { status: "waiting" }
  | { status: "submitting" }
  | { status: "submitted"; queuePosition: number | null; runId: number }
  | { status: "failed"; message: string };

interface RerunModalProps {
  onClose: () => void;
  /** Preselects these ids once each is confirmed present in the loaded
   * eligible list -- avoids a race with the picker query's own load. */
  initialManuscriptIds?: number[];
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

export function RerunModal({ onClose, initialManuscriptIds }: RerunModalProps) {
  const { data: manuscripts, isPending: manuscriptsPending, isError: manuscriptsError, refetch } = useManuscripts();
  const { data: families, isPending: familiesPending } = useRubricFamilies();
  const activeFamily = (families ?? []).find((f) => f.is_active);

  // ux-critic finding (P1, live-reproduced against real seeded data with
  // two simultaneously-active rubric families): a manuscript whose
  // latest done run was under a COMPLETELY UNRELATED format must never
  // be treated as eligible here -- without this filter it defaulted to
  // selected and could silently submit for grading against a rubric it
  // was never checked against, burning real quota (D-001) with nothing
  // in the row to warn the instructor. Manuscripts done under an EARLIER
  // VERSION OF THE SAME FAMILY remain eligible (the whole point of this
  // ticket); "same family" is the only thing that changes.
  //
  // V-064 (AC5): the SAME class of exclusion, extended to program --
  // silently proceeding here is exactly the client-side gap V-041 itself
  // found once already (a server-side fix alone wasn't enough). A
  // manuscript's program can change AFTER its last check ran under this
  // family (ticket edge case) without a real submission ever reaching
  // the mismatched-program server guard, so this must be checked here
  // too, not just trusted to the create-check-run endpoint.
  const eligible = useMemo(
    () =>
      (manuscripts ?? []).filter(
        (m) =>
          m.latest_done_check_run_id !== null &&
          m.latest_done_rubric_family_id !== null &&
          m.latest_done_rubric_family_id === activeFamily?.rubric_family_id &&
          isRubricEligibleForProgram(m.program, activeFamily?.program) &&
          !(m.latest_check_run_status && RUNNING_STATUSES.has(m.latest_check_run_status)),
      ),
    [manuscripts, activeFamily],
  );

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [seededInitial, setSeededInitial] = useState(false);
  // Set once loading has genuinely settled AND at least one requested id
  // never made it into `eligible` -- lets the UI say WHY a manuscript the
  // instructor explicitly asked to re-run isn't in the list, instead of
  // silently showing an empty or unrelated selection.
  const [excludedInitialCount, setExcludedInitialCount] = useState(0);
  const [rowState, setRowState] = useState<Record<number, RowState>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const createCheckRun = useCreateCheckRun();
  const dataReady = !manuscriptsPending && !manuscriptsError && !familiesPending && activeFamily !== undefined;

  // Default: everything eligible is selected, since a version this
  // manuscript was graded against can never be the one just activated
  // (each version is its own row/id) -- every eligible manuscript is,
  // by construction, stale relative to what's active now. Seeded once
  // loading has genuinely settled, not just once `eligible` is non-empty
  // (a genuinely-empty eligible set, e.g. every requested id belonged to
  // another family, must still resolve instead of retrying forever), so
  // the instructor's own deselections survive a background refetch.
  useEffect(() => {
    if (seededInitial || !dataReady) return;
    if (initialManuscriptIds && initialManuscriptIds.length > 0) {
      const present = initialManuscriptIds.filter((id) => eligible.some((m) => m.id === id));
      setSelected(new Set(present));
      setExcludedInitialCount(initialManuscriptIds.length - present.length);
      setSeededInitial(true);
      return;
    }
    setSelected(new Set(eligible.map((m) => m.id)));
    setSeededInitial(true);
  }, [eligible, initialManuscriptIds, seededInitial, dataReady]);

  // Debounced so toggling several checkboxes quickly doesn't fire the
  // estimate endpoint once per click. `debouncedIds` is compared against
  // the LIVE selection by CONTENT (sorted key), not by length -- a
  // swap-pattern edit within the debounce window (deselect one, select a
  // different one, net count unchanged) previously left a length-only
  // check reporting "settled" while still describing the previous
  // selection (ux-critic finding, P2: a real quota estimate could be
  // shown as final for the wrong set of manuscripts for up to 400ms).
  const [debouncedIds, setDebouncedIds] = useState<number[]>([]);
  const selectedKey = [...selected].sort((a, b) => a - b).join(",");
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedIds([...selected]), 400);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey]);
  const debouncedKey = [...debouncedIds].sort((a, b) => a - b).join(",");

  const estimate = useRerunEstimate(debouncedIds, activeFamily?.id);
  const estimatePending = estimate.isPending || debouncedKey !== selectedKey;

  // Native `checked` can't express partial selection -- `indeterminate`
  // is DOM-only, set imperatively (ux-critic finding, P2: without this,
  // "some selected" and "none selected" rendered identically, both as a
  // plain unchecked box, on the one control meant to summarize either).
  const selectAllRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!selectAllRef.current) return;
    selectAllRef.current.indeterminate = selected.size > 0 && selected.size < eligible.length;
  }, [selected, eligible.length]);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === eligible.length ? new Set() : new Set(eligible.map((m) => m.id))));
  }

  async function submit(ids: number[]) {
    if (!activeFamily) return;
    setIsSubmitting(true);
    setHasSubmitted(true);
    try {
      let succeeded = 0;
      let failed = 0;
      for (const id of ids) {
        setRowState((prev) => ({ ...prev, [id]: { status: "submitting" } }));
        try {
          const run = await createCheckRun.mutateAsync({ manuscript_id: id, rubric_id: activeFamily.id });
          setRowState((prev) => ({
            ...prev,
            [id]: { status: "submitted", queuePosition: run.queue_position, runId: run.id },
          }));
          succeeded++;
        } catch (err) {
          const message = err instanceof ApiError ? err.message : "Could not start this check. Try again.";
          setRowState((prev) => ({ ...prev, [id]: { status: "failed", message } }));
          failed++;
        }
      }
      setAnnouncement(
        `Re-run submissions complete. ${succeeded} started successfully${failed > 0 ? `, ${failed} failed` : ""}.`,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  const selectedList = [...selected];
  const failedIds = Object.entries(rowState)
    .filter(([, s]) => s.status === "failed")
    .map(([id]) => Number(id));
  const allDone = hasSubmitted && !isSubmitting;
  const allFailed = allDone && failedIds.length === selectedList.length && selectedList.length > 0;

  const submittedRows = hasSubmitted
    ? eligible.filter((m) => selectedList.includes(m.id))
    : eligible;

  let footer: React.ReactNode;
  if (!hasSubmitted) {
    footer = (
      <>
        <button
          type="button"
          onClick={onClose}
          className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => submit(selectedList)}
          disabled={selectedList.length === 0 || !activeFamily}
          className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-45"
        >
          Re-run {selectedList.length} {selectedList.length === 1 ? "manuscript" : "manuscripts"}
        </button>
      </>
    );
  } else if (isSubmitting) {
    footer = (
      <button
        type="button"
        disabled
        aria-busy="true"
        className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action opacity-60"
      >
        Submitting.
      </button>
    );
  } else if (allFailed) {
    footer = (
      <>
        <button type="button" onClick={onClose} className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg">
          Close
        </button>
        <button type="button" onClick={() => submit(selectedList)} className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover">
          Try again
        </button>
      </>
    );
  } else if (failedIds.length > 0) {
    footer = (
      <>
        <button type="button" onClick={onClose} className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg">
          Close
        </button>
        <button type="button" onClick={() => submit(failedIds)} className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover">
          Retry failed ({failedIds.length})
        </button>
      </>
    );
  } else {
    footer = (
      <button type="button" onClick={onClose} className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover">
        Done
      </button>
    );
  }

  return (
    <ModalBackdrop>
      <Modal title="Re-run manuscripts" onClose={isSubmitting ? undefined : onClose} size="lg" footer={footer}>
        <div className="flex flex-col gap-3" aria-busy={isSubmitting}>
          <p aria-live="polite" className="sr-only">
            {announcement}
          </p>

          {manuscriptsPending || familiesPending ? (
            <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 text-sm text-ink-secondary">
              <SpinnerIcon />
              Loading manuscripts.
            </div>
          ) : manuscriptsError ? (
            <div role="alert" className="rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text">
              Could not load your manuscripts.{" "}
              <button type="button" onClick={() => refetch()} className="font-medium underline">
                Try again
              </button>
              .
            </div>
          ) : !activeFamily ? (
            <p className="rounded-md border border-border bg-page px-3 py-2.5 text-sm text-ink-secondary">
              No active rubric is available right now. Activate a format version before re-running
              manuscripts.
            </p>
          ) : eligible.length === 0 ? (
            <p className="rounded-md border border-border bg-page px-3 py-2.5 text-sm text-ink-secondary">
              {(manuscripts ?? []).some((m) => m.latest_done_check_run_id !== null)
                ? `No manuscripts have been checked against ${activeFamily.title} yet, so there's nothing to re-run.`
                : "No manuscripts have been checked yet, so there's nothing to re-run."}
            </p>
          ) : (
            <>
              <p className="rounded-md bg-status-info-bg px-3 py-2 text-sm text-status-info-text">
                Runs against <b>{activeFamily.title}</b> v{activeFamily.version}, {activeFamily.criteria_count}{" "}
                criteria.
              </p>

              {/* V-071 (newcomer finding, live-reproduced): this paragraph
                  used to unconditionally claim "all manuscripts... selected
                  by default" even when opened from a single row's Re-run
                  button, where only that one manuscript is preselected --
                  the checkboxes were right and the sentence describing them
                  was wrong. */}
              {!hasSubmitted &&
                (initialManuscriptIds && initialManuscriptIds.length > 0 ? (
                  // A count fixed at seeding time, not a live read of
                  // `selected` -- the sentence describes what was selected
                  // BY DEFAULT, and must not silently start describing
                  // whatever the instructor has toggled since. If nothing
                  // requested actually made it in (every id excluded), the
                  // paragraph below this one already explains that; saying
                  // "selected by default" here too would contradict it.
                  initialManuscriptIds.length - excludedInitialCount > 0 && (
                    <p className="text-sm text-ink-secondary">
                      Only the manuscript{initialManuscriptIds.length - excludedInitialCount === 1
                        ? ""
                        : "s"}{" "}
                      you chose{" "}
                      {initialManuscriptIds.length - excludedInitialCount === 1 ? "is" : "are"}{" "}
                      selected by default. Select others below if you want to re-run them too.
                    </p>
                  )
                ) : (
                  <p className="text-sm text-ink-secondary">
                    All manuscripts previously checked against {activeFamily.title} are selected by
                    default. Deselect any you don't want to re-run.
                  </p>
                ))}

              {!hasSubmitted && excludedInitialCount > 0 && (
                <p className="rounded-md bg-status-attention-bg px-3 py-2 text-sm text-status-attention-text">
                  The manuscript{excludedInitialCount === 1 ? "" : "s"} you selected{" "}
                  {excludedInitialCount === 1 ? "isn't" : "aren't"} included below: either{" "}
                  {excludedInitialCount === 1 ? "it wasn't" : "they weren't"} checked against{" "}
                  {activeFamily.title}, or {excludedInitialCount === 1 ? "its" : "their"} program no
                  longer matches this rubric's.
                </p>
              )}

              <div className="flex flex-col rounded-md border border-border">
                {!hasSubmitted && (
                  <label className="flex min-h-11 items-center gap-2 border-b border-border px-3 text-sm font-medium text-ink">
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={selected.size === eligible.length}
                      onChange={toggleAll}
                    />
                    Select all
                  </label>
                )}
                {submittedRows.map((m) => {
                  const identity = manuscriptIdentity(m.group_label, m.original_filename);
                  const { label, tone } = manuscriptStatus(m);
                  const state = rowState[m.id];

                  const identityBlock = (
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-ink" title={identity.primary}>
                        {identity.primary}
                      </span>
                      {identity.secondary && (
                        <span className="block truncate text-xs text-ink-tertiary" title={identity.secondary}>
                          {identity.secondary}
                        </span>
                      )}
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-tertiary">
                        {!hasSubmitted && (
                          <>
                            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold bg-status-${tone}-bg text-status-${tone}-text`}>
                              {label}
                            </span>
                            <span>Last checked {formatDate(m.created_at)}</span>
                          </>
                        )}
                        {hasSubmitted && state?.status === "submitted" && (
                          <>
                            <span>
                              {state.queuePosition && state.queuePosition > 0
                                ? `Queue position ${state.queuePosition}.`
                                : "Starting now."}
                            </span>
                            <a href={`/checks/${state.runId}`} className="font-medium text-link underline">
                              View progress
                            </a>
                          </>
                        )}
                        {hasSubmitted && state?.status === "failed" && <span className="text-status-danger-text">{state.message}</span>}
                      </span>
                    </span>
                  );

                  if (hasSubmitted) {
                    // No form control once submitted (the checkbox is
                    // replaced by a static status indicator) -- a plain
                    // row, not a <label>, since there's nothing left to
                    // associate a label WITH.
                    return (
                      <div key={m.id} className="flex min-h-11 items-start gap-3 border-b border-border px-3 py-2.5 text-sm last:border-0">
                        <span className="mt-0.5 flex-none">
                          {!state || state.status === "waiting" ? (
                            <span className="text-xs text-ink-tertiary">Waiting</span>
                          ) : state.status === "submitting" ? (
                            <span className="inline-flex items-center gap-1 text-xs text-status-info-text">
                              <SpinnerIcon /> Submitting.
                            </span>
                          ) : state.status === "submitted" ? (
                            <span className="text-xs font-medium text-status-success-text">Submitted</span>
                          ) : (
                            <span className="text-xs font-medium text-status-danger-text">Failed</span>
                          )}
                        </span>
                        {identityBlock}
                      </div>
                    );
                  }

                  // The row's only interactive control is the checkbox,
                  // so wrapping the whole row in <label> gives it a real
                  // accessible name (the manuscript's own identity text)
                  // instead of an anonymous checkbox, and doubles as a
                  // larger touch target (WCAG 2.5.8 / mobile thumb-target
                  // guidance) -- found missing in this session's own
                  // test-writing (a row checkbox had no accessible name
                  // at all until this fix).
                  return (
                    <label key={m.id} className="flex min-h-11 items-start gap-3 border-b border-border px-3 py-2.5 text-sm last:border-0">
                      <input
                        type="checkbox"
                        checked={selected.has(m.id)}
                        onChange={() => toggle(m.id)}
                        // ux-critic finding, P3: without this, the
                        // wrapping <label>'s full text (identity + status
                        // pill + date) became the accessible name --
                        // correct information, but a much longer
                        // announcement per row than a sighted user scans
                        // in under a second across ~20 rows. Overrides
                        // to just the identity, per WAI-ARIA's own
                        // accname precedence (aria-label wins over label
                        // content).
                        aria-label={identity.primary}
                        className="mt-0.5"
                      />
                      {identityBlock}
                    </label>
                  );
                })}
              </div>

              {!hasSubmitted && selected.size > 0 && (
                <div
                  role={estimate.isError ? "alert" : "status"}
                  aria-live="polite"
                  aria-busy={estimatePending}
                  className={
                    estimate.isError
                      ? "rounded-md border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm text-status-attention-text"
                      : "rounded-md bg-status-info-bg px-3 py-2 text-sm text-status-info-text"
                  }
                >
                  {estimate.isError ? (
                    <>
                      Could not estimate AI usage right now.{" "}
                      <button type="button" onClick={() => estimate.refetch()} className="font-medium underline">
                        Try again
                      </button>
                      .
                    </>
                  ) : estimatePending ? (
                    <span className="inline-flex items-center gap-2">
                      <SpinnerIcon /> Estimating AI usage.
                    </span>
                  ) : estimate.data && estimate.data.manuscripts_with_no_estimate === 0 ? (
                    <>
                      Estimated AI usage: <b>{estimate.data.total_estimated_calls}</b>{" "}
                      {estimate.data.total_estimated_calls === 1 ? "call" : "calls"} across{" "}
                      {selected.size} {selected.size === 1 ? "manuscript" : "manuscripts"}.
                    </>
                  ) : estimate.data && estimate.data.total_estimated_calls > 0 ? (
                    <>
                      Estimated AI usage: <b>{estimate.data.total_estimated_calls}</b>{" "}
                      {estimate.data.total_estimated_calls === 1 ? "call" : "calls"} across{" "}
                      {selected.size - estimate.data.manuscripts_with_no_estimate} of {selected.size}{" "}
                      selected manuscripts. {estimate.data.manuscripts_with_no_estimate}{" "}
                      {estimate.data.manuscripts_with_no_estimate === 1 ? "has" : "have"} no estimate
                      available (never checked against this format before, or its last check made no
                      AI calls), and {estimate.data.manuscripts_with_no_estimate === 1 ? "is" : "are"}{" "}
                      not included in this total.
                    </>
                  ) : (
                    <>
                      No AI usage estimate is available for the {selected.size} selected{" "}
                      {selected.size === 1 ? "manuscript" : "manuscripts"} (they were never checked
                      against this format before, or their last check made no AI calls). You can
                      still proceed.
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
