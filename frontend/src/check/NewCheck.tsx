// Screen 4f — New manuscript check (modal, F3.1-F3.6). Manuscripts are
// uploaded/ingested separately (V-008); this modal only picks WHICH
// already-ingested manuscript to run against WHICH active rubric.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { Modal, ModalBackdrop } from "../components/Modal";
import { formatManuscriptOption, manuscriptIdentity } from "../domain/manuscriptLabel";
import { isRubricEligibleForProgram } from "../domain/rubricEligibility";
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
  // BUG-040: absent (not a no-op () => {}) for a structural blocker with
  // nowhere in THIS modal to jump to — rendering these as an underlined
  // button promised an action the control didn't deliver (Tab lands on
  // it, Enter does nothing observable, a false affordance). V-059 gives
  // "no manuscripts" a real destination (`onUploadManuscript`, when the
  // caller supplies one); "no active rubric" still has none, so it stays
  // plain text on purpose, not because this field can't hold one.
  action?: () => void;
}

export function NewCheckModal({
  onClose,
  initialManuscriptId,
  onUploadManuscript,
}: {
  onClose: () => void;
  /** Preselects a just-uploaded manuscript (V-059's Dashboard handoff) --
   * only applied once it's actually present in the loaded picker list, so
   * a race with the invalidation refetch can't seed a stale/bad id. */
  initialManuscriptId?: number;
  /** When supplied, the "no manuscripts" blocker becomes a real action
   * instead of a dead-end sentence (BUG-039/040's own deferred fix). */
  onUploadManuscript?: () => void;
}) {
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

  // V-059: preselects a manuscript just uploaded via the Dashboard's
  // upload-then-check handoff. Waits for it to actually appear in the
  // loaded, ready list rather than trusting the id blindly -- the
  // invalidated picker query and this modal's mount aren't guaranteed to
  // race in a particular order. An explicit pick always wins (same rule
  // already used for `rubricId`), so this only ever fires once, before
  // the instructor has chosen anything themselves.
  useEffect(() => {
    if (initialManuscriptId === undefined) return;
    if (manuscriptId !== "") return;
    if (readyManuscripts.some((m) => m.id === initialManuscriptId)) {
      setManuscriptId(initialManuscriptId);
    }
  }, [initialManuscriptId, readyManuscripts, manuscriptId]);

  // BUG-039: a "no active rubric"/"no manuscripts" block is a fact about
  // the account, not something the instructor forgot to fill in — it
  // shouldn't wait for a Start-check click to be discoverable. Once data
  // has genuinely loaded (not pending, not errored), auto-run the SAME
  // focus path a click would, so a keyboard/screen-reader user reaching
  // this modal's Close button and tabbing forward lands on the
  // explanation immediately, worded identically to (not differently
  // from) whatever a later Start-check click would show. Deliberately
  // does NOT set `submitAttempted` — `currentProblems` below already
  // lets structural problems through unconditionally, so this stays
  // decoupled from the "you haven't chosen a manuscript/rubric yet"
  // validation, which must still wait for a real Start-check attempt
  // (GOV.UK's own guidance this codebase already follows elsewhere).
  // `isPending` (TanStack Query) only ever transitions true -> false
  // once per query lifetime here (background refetches use
  // `isFetching`, not `isPending`), so this can't re-fire on refetch.
  useEffect(() => {
    if (manuscriptsPending || familiesPending || manuscriptsError || familiesError) return;
    const structural = computeProblems().some(
      (p) => p.id === "no-manuscripts" || p.id === "no-rubric",
    );
    if (!structural) return;
    setFocusSummaryToken((t) => t + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manuscriptsPending, familiesPending, manuscriptsError, familiesError]);

  const selectedManuscript = readyManuscripts.find((m) => m.id === manuscriptId);

  // V-064 (AC2/AC4): once a manuscript is chosen, narrow to only the
  // rubrics eligible for ITS program -- not silently, the excluded ones
  // still exist in `activeFamilies` for the "why isn't X offered"
  // explanation below. Before a manuscript is chosen there's nothing to
  // filter against yet, so every active family is shown.
  const eligibleFamilies = selectedManuscript
    ? activeFamilies.filter((f) => isRubricEligibleForProgram(selectedManuscript.program, f.program))
    : activeFamilies;
  const familiesForPicker = selectedManuscript ? eligibleFamilies : activeFamilies;

  // Auto-selects the sole ELIGIBLE rubric once it loads (most instructors
  // have exactly one, or exactly one that fits this manuscript's
  // program); an explicit pick always wins once made, and is dropped if
  // it stops being eligible (a later manuscript change made it
  // ineligible) rather than silently submitted anyway. Derived per
  // render rather than synced via an effect — `families` arrives
  // asynchronously, so a one-time useState initializer would miss it.
  const rubricId: number | "" =
    chosenRubricId !== "" && eligibleFamilies.some((f) => f.id === chosenRubricId)
      ? chosenRubricId
      : eligibleFamilies.length === 1
        ? eligibleFamilies[0].id
        : "";
  const selectedRubric = eligibleFamilies.find((f) => f.id === rubricId);
  // AC3: a manuscript with no program set is never told "nothing fits" —
  // it's eligible for everything, so this only ever fires when the
  // manuscript HAS a program and nothing active matches it.
  const noEligibleRubricForProgram =
    selectedManuscript?.program != null && activeFamilies.length > 0 && eligibleFamilies.length === 0;
  // ux-critic finding (V-064 review, live-reproduced): the FULL-exclusion
  // case above is explained, but a PARTIAL one wasn't -- with 2+ active
  // rubrics and exactly 1 eligible, the picker (gated on
  // `familiesForPicker.length > 1`) never renders at all, so the excluded
  // rubric(s) existing was invisible, contradicting AC4's own "explained,
  // not silently hidden" for this in-between case just as much as the
  // zero-eligible one.
  const excludedForProgramCount = selectedManuscript ? activeFamilies.length - eligibleFamilies.length : 0;

  function computeProblems(): Problem[] {
    const problems: Problem[] = [];
    if (readyManuscripts.length === 0) {
      problems.push({
        id: "no-manuscripts",
        message: "No manuscripts are available to check yet.",
        action: onUploadManuscript,
      });
    } else if (manuscriptId === "") {
      problems.push({ id: "manuscript", message: "Choose a manuscript.", action: () => manuscriptSelectRef.current?.focus() });
    }
    if (activeFamilies.length === 0) {
      problems.push({ id: "no-rubric", message: "No active rubric is available yet." });
    } else if (noEligibleRubricForProgram) {
      // AC4: explained, not silently hidden -- names the manuscript's
      // own program so the instructor can see WHY nothing was offered,
      // not just that nothing was.
      problems.push({
        id: "no-eligible-rubric",
        message: `No active rubric is set up for this manuscript's program (${selectedManuscript!.program}). Set a rubric's program on the rubric management screen, or clear this manuscript's program.`,
      });
    } else if (eligibleFamilies.length > 1 && rubricId === "") {
      problems.push({
        id: "rubric",
        message: "Choose which active rubric to check against.",
        action: () => rubricSelectRef.current?.focus(),
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

  // BUG-039: structural blockers ("no manuscripts"/"no active rubric")
  // are facts, not something to validate only after an attempted
  // submit — they show as soon as they're genuinely known (not while
  // still loading, and not during a fetch error — those already have
  // their own dedicated loading/error UI below, and `readyManuscripts`/
  // `activeFamilies` are both empty in those states too, which would
  // otherwise wrongly surface "no manuscripts"/"no active rubric" on
  // top of the real loading/error message). Choice blockers ("choose a
  // manuscript"/"choose a rubric") still wait for `submitAttempted`,
  // unchanged.
  const currentProblems = computeProblems().filter((p) => {
    if (submitAttempted) return true;
    if (p.id === "no-manuscripts") return !manuscriptsPending && !manuscriptsError;
    if (p.id === "no-rubric") return !familiesPending && !familiesError;
    // Same reasoning as "no-manuscripts"/"no-rubric": a fact about this
    // manuscript's program, not something the instructor forgot to fill
    // in -- visible as soon as it's known, not gated on a submit attempt.
    if (p.id === "no-eligible-rubric") return !familiesPending && !familiesError;
    return false;
  });
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
        <div className="signal-group-flow">
          {currentProblems.length > 0 && (
            <div
              ref={summaryRef}
              tabIndex={-1}
              role="alert"
              className="rounded-md border-2 border-status-attention-text/40 bg-status-attention-bg p-3.5 text-sm text-status-attention-text"
            >
              <p className="font-semibold">Fix the following before starting this check:</p>
              <ul className="mt-1.5 list-disc pl-5">
                {currentProblems.map((p) => (
                  <li key={p.id}>
                    {p.action ? (
                      <button type="button" onClick={p.action} className="text-link underline hover:text-link-hover">
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
                      {formatManuscriptOption(
                        manuscriptIdentity(m.group_label, m.original_filename),
                        dateFormatter.format(new Date(m.created_at)),
                      )}
                    </option>
                  ))}
                </select>
                {manuscriptInvalid && (
                  <p id="manuscript-err" className="text-sm text-status-attention-text">
                    Choose a manuscript.
                  </p>
                )}
                {selectedManuscript &&
                  (() => {
                    const identity = manuscriptIdentity(
                      selectedManuscript.group_label,
                      selectedManuscript.original_filename,
                    );
                    return (
                      <p className="text-sm text-ink-secondary break-words">
                        Selected: <b className="font-medium text-ink">{identity.primary}</b>
                        {identity.secondary && <> ({identity.secondary})</>}, uploaded{" "}
                        {dateFormatter.format(new Date(selectedManuscript.created_at))}.
                      </p>
                    );
                  })()}
                {/* AC3: not a blocker -- an unset program never narrows the
                    rubric choice, this just tells the instructor why every
                    active rubric is still on offer. */}
                {selectedManuscript && selectedManuscript.program == null && (
                  <p className="text-sm text-ink-tertiary">
                    This manuscript's program is not set, so every active rubric is offered.
                  </p>
                )}
              </>
            )}
          </div>

          {familiesForPicker.length > 1 && (
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
                    {familiesForPicker.map((f) => (
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

          {/* V-064 (ux-critic finding, AC4): with exactly one eligible
              rubric, the picker above never renders at all (its own
              length > 1 gate) -- without this, an excluded rubric's
              existence was invisible whenever at least one other rubric
              remained eligible, same "explained, not silently hidden"
              gap AC4 already covers for the zero-eligible case. */}
          {excludedForProgramCount > 0 && eligibleFamilies.length > 0 && (
            <p className="text-sm text-ink-tertiary">
              {excludedForProgramCount} other active rubric{excludedForProgramCount === 1 ? "" : "s"} not
              shown: {excludedForProgramCount === 1 ? "its" : "their"} program doesn't match this
              manuscript's ({selectedManuscript!.program}).
            </p>
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
