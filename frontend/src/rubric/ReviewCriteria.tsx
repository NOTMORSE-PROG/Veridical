// Screen 4d — Review parsed criteria, the HITL gate (F2.3). Nothing runs
// against a rubric until the instructor confirms here — this screen IS
// the human half of the "any format" objective. V-055: fixes BUG-021
// (criterion/evidence inputs didn't fill their grid column — a bare
// `1fr` track's minimum is its content's intrinsic size per the CSS Grid
// spec, so `minmax(0, 1fr)` is the actual fix, same pattern already
// proven on ManuscriptsTable.tsx's Group column) plus a real WAI-ARIA
// radio-group type toggle, an accessible error-summary validation flow,
// and full loading/error states with real focus/live-region handling.
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router";
import type { CriterionType } from "../api/types";
import { Chip } from "../components/Chip";
import { cx } from "../components/cx";
import { Modal, ModalBackdrop } from "../components/Modal";
import { WeightImportanceTag, type WeightImportance } from "../components/WeightImportanceTag";
import { useRouteFocus } from "../routing/useRouteFocus";
import { type CriterionEdit, useRubric, useSaveCriteria } from "./useRubric";

// D-023: mirrors `app/report/weight_importance.py`'s default ratios (a
// live preview as the instructor types, not the authoritative bucket --
// that's computed server-side per report from `WEIGHT_IMPORTANCE_*`
// settings, which this screen has no way to read at request time).
// Deliberately the SAME numbers as the backend's own defaults so the
// live preview matches what the report will actually show in the
// common case.
const WEIGHT_IMPORTANCE_LOW_MAX_RATIO = 0.5;
const WEIGHT_IMPORTANCE_HIGH_MIN_RATIO = 1.5;

function liveWeightImportance(weight: number, averageWeight: number): WeightImportance {
  if (averageWeight <= 0) return "med";
  const ratio = weight / averageWeight;
  if (ratio < WEIGHT_IMPORTANCE_LOW_MAX_RATIO) return "low";
  if (ratio >= WEIGHT_IMPORTANCE_HIGH_MIN_RATIO) return "high";
  return "med";
}

interface Row {
  key: string;
  id: number | null;
  type: CriterionType;
  text: string;
  evidence: string;
  weight: number;
}

type FocusIntent =
  | { type: "criterion-text"; index: number }
  | { type: "add-criterion" }
  | { type: "remove-button"; index: number }
  | { type: "summary" }
  | null;

const TYPE_OPTIONS: readonly CriterionType[] = ["structural", "semantic"];
const TYPE_LABELS: Record<CriterionType, string> = { structural: "Structural", semantic: "Semantic" };

let newRowCounter = 0;

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function AttentionIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3.5 21 19.5H3Z" />
      <line x1="12" y1="10" x2="12" y2="14" />
      <circle cx="12" cy="17" r="0.5" fill="currentColor" />
    </svg>
  );
}

function SuccessIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.5 2.5L16 9" />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="5" y1="5" x2="19" y2="19" />
      <line x1="19" y1="5" x2="5" y2="19" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

// Auto-growing single-line-by-default textarea: makes truncation of a
// long criterion/evidence string categorically impossible (wraps and
// grows) rather than just less likely (a wider fixed input still clips
// eventually) — the real structural fix BUG-021 asked for, not just a
// wider box.
function AutoGrowTextarea({
  value,
  onChange,
  placeholder,
  className,
  disabled,
  "aria-label": ariaLabel,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  "aria-label": string;
  "aria-invalid"?: "true" | undefined;
  "aria-describedby"?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function grow(el: HTMLTextAreaElement) {
    // scrollHeight never includes border (only content+padding), so on
    // a border-box element it under-measures by the border width —
    // without this the box is ~2px short of its own content, which
    // reads as a permanent phantom scrollbar on every field.
    const style = getComputedStyle(el);
    const borderY =
      style.boxSizing === "border-box"
        ? parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
        : 0;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + borderY}px`;
  }

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    grow(el);
  }, [value]);

  // A value-only dependency misses reflow that changes wrapped line
  // count without changing the text itself — a column narrowing on
  // viewport resize (desktop grid -> mobile card) is exactly that case,
  // and left already-typed text clipped until the next keystroke. A
  // ResizeObserver on the textarea itself was tried first and rejected:
  // it observes the exact element `grow()` mutates, which hits the
  // browser's own resize-observer-loop guard and silently drops
  // notifications under rapid resizing (confirmed live via ux-critic,
  // V-055) — listening on `window` sidesteps that class of bug entirely.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    function onResize() {
      if (el) grow(el);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-invalid={ariaInvalid}
      aria-describedby={ariaDescribedBy}
      className={className}
    />
  );
}

// WAI-ARIA APG Radio pattern (w3.org/WAI/ARIA/apg/patterns/radio/),
// reimplemented with VERIDICAL's tokens — replaces the old two-independent
// -toggle-buttons SegmentedToggle, which modeled `aria-pressed` (two
// independent booleans) for what is actually one mutually-exclusive
// choice.
function TypeRadioGroup({
  value,
  onChange,
  ariaLabel,
  fill,
  disabled,
}: {
  value: CriterionType;
  onChange: (t: CriterionType) => void;
  ariaLabel: string;
  fill?: boolean;
  disabled?: boolean;
}) {
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "ArrowLeft"
        ? (index + TYPE_OPTIONS.length - 1) % TYPE_OPTIONS.length
        : event.key === "ArrowRight"
          ? (index + 1) % TYPE_OPTIONS.length
          : event.key === "Home"
            ? 0
            : TYPE_OPTIONS.length - 1;
    const nextType = TYPE_OPTIONS[next];
    onChange(nextType);
    document.getElementById(`type-${ariaLabel}-${nextType}`)?.focus();
  }

  return (
    <span
      role="radiogroup"
      aria-label={ariaLabel}
      className={cx("inline-flex overflow-hidden rounded-md border border-border-input", fill && "w-full")}
    >
      {TYPE_OPTIONS.map((opt, index) => (
        <button
          key={opt}
          id={`type-${ariaLabel}-${opt}`}
          type="button"
          role="radio"
          aria-checked={opt === value}
          aria-disabled={disabled ? "true" : undefined}
          tabIndex={opt === value ? 0 : -1}
          onKeyDown={(event) => onKeyDown(event, index)}
          onClick={() => !disabled && onChange(opt)}
          className={cx(
            "px-3 text-sm font-medium",
            fill ? "min-h-11 flex-1" : "h-8",
            disabled
              ? "cursor-default bg-page text-ink-tertiary"
              : opt === value
                ? "bg-action text-on-action"
                : "bg-panel text-ink-secondary hover:bg-status-neutral-bg",
          )}
        >
          {TYPE_LABELS[opt]}
        </button>
      ))}
    </span>
  );
}

export function ReviewCriteriaPage() {
  const { rubricId } = useParams<{ rubricId: string }>();
  const id = Number(rubricId);
  const { data: rubric, isPending, isError, refetch } = useRubric(id);
  const save = useSaveCriteria(id);
  const [rows, setRows] = useState<Row[] | null>(null);
  // BUG-037: the last-saved snapshot rows are compared against, to
  // detect edits that were never persisted (never a background refetch
  // or the initial load alone — only a successful save moves this).
  const [lastSavedRows, setLastSavedRows] = useState<Row[] | null>(null);
  const [attempted, setAttempted] = useState<"save" | "confirm" | null>(null);
  const [removedAnnouncement, setRemovedAnnouncement] = useState("");
  const [focusIntent, setFocusIntent] = useState<FocusIntent>(null);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const addCriterionRef = useRef<HTMLButtonElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  useRouteFocus("Review parsed criteria - VERIDICAL", headingRef);

  // Focus-after-state-change goes through a dedicated intent + effect,
  // not a raw `requestAnimationFrame` call from inside the event
  // handler: two rAF calls scheduled a few milliseconds apart (e.g. a
  // remove immediately followed by a Save click) are not guaranteed to
  // resolve in registration order — confirmed live as a genuine,
  // reproducible race (final focus landing on the wrong element roughly
  // 1 in 3 runs). A `useEffect` keyed to this state instead runs
  // exactly once per committed change, after React's own commit, with
  // no timer race.
  useEffect(() => {
    if (!focusIntent) return;
    if (focusIntent.type === "add-criterion") {
      addCriterionRef.current?.focus();
    } else if (focusIntent.type === "summary") {
      summaryRef.current?.focus();
    } else if (focusIntent.type === "criterion-text") {
      focusCriterionText(focusIntent.index);
    } else if (focusIntent.type === "remove-button") {
      queryVisible<HTMLElement>('[aria-label^="Remove criterion"]')[focusIntent.index]?.focus();
    }
    setFocusIntent(null);
  }, [focusIntent]);

  // Seed local edit state once, when the rubric first loads — a
  // background refetch must not clobber in-progress edits.
  useEffect(() => {
    if (rubric && rows === null) {
      const seeded = rubric.criteria.map((c) => ({
        key: String(c.id),
        id: c.id,
        type: c.type,
        text: c.text,
        evidence: c.evidence ?? "",
        weight: c.weight,
      }));
      setRows(seeded);
      // BUG-037: the last-PERSISTED snapshot, not just the last-loaded
      // one — updated again on every successful save below. Comparing
      // `rows` against this (not against `null`/mount) is what lets
      // in-progress, never-saved edits be detected as unsaved work.
      setLastSavedRows(seeded);
    }
  }, [rubric, rows]);

  // BUG-037: unsaved edits were silently discarded on in-app navigation
  // (a Dashboard nav-link click, no warning) — directly undermining the
  // screen's own "Nothing runs until you confirm" promise and the HITL
  // safety-net premise (V-012) that an instructor fixing a bad parse IS
  // the safety net. A version another version has superseded (V-013
  // F2.4) can't be edited at all, so it's never dirty. `useBlocker`
  // (and therefore this hook call) must run unconditionally on every
  // render — it's declared here, before this component's early
  // (loading/error) returns below, not alongside the render logic that
  // reads `rubric`/`rows` freely.
  const editable = rubric ? rubric.is_latest_version : false;
  const isDirty =
    editable &&
    rows !== null &&
    lastSavedRows !== null &&
    JSON.stringify(rows) !== JSON.stringify(lastSavedRows);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty && currentLocation.pathname !== nextLocation.pathname,
  );
  // ux-critic finding (BUG-037 review): a rapid double-click on either
  // footer button called blocker.proceed()/reset() a second time before
  // the dialog unmounted, and react-router's own invariant throws on a
  // transition out of "blocked" that's already left that state
  // ("Invalid blocker state transition: unblocked -> proceeding"). A
  // ref, not state, since two synchronous clicks in the same task both
  // read a re-render-pending `blocker.state` before either commit —
  // only a ref write is visible to the second click's handler
  // immediately.
  const blockerResolvingRef = useRef(false);
  useEffect(() => {
    if (blocker.state === "blocked") blockerResolvingRef.current = false;
  }, [blocker.state]);
  function resolveBlocker(action: "reset" | "proceed") {
    if (blockerResolvingRef.current || blocker.state !== "blocked") return;
    blockerResolvingRef.current = true;
    if (action === "reset") {
      blocker.reset();
    } else {
      blocker.proceed();
    }
  }

  function updateRow(key: string, patch: Partial<Row>) {
    setRows((current) => current?.map((r) => (r.key === key ? { ...r, ...patch } : r)) ?? current);
  }

  // `type="text"` + `inputmode="decimal"` (GOV.UK's own numeric-input
  // pattern, RESEARCH.md) rather than `type="number"`: the native
  // spinner/scroll-wheel-changes-value behavior is platform-inconsistent
  // chrome this rebuild has deliberately removed everywhere else (4c's
  // file trigger). A bare regex keeps only well-formed decimal input.
  function handleWeightChange(key: string, raw: string) {
    if (raw === "" || /^\d*\.?\d*$/.test(raw)) {
      updateRow(key, { weight: raw === "" || raw === "." ? 0 : Number(raw) });
    }
  }

  // Desktop table and mobile card both render at once (CSS hides
  // whichever doesn't apply), so an unscoped selector matches two
  // elements sharing the same aria-label — the hidden desktop one
  // always wins document order, and focusing a display:none element is
  // a silent no-op. Every focus-target lookup must filter to the one
  // actually visible at the current breakpoint.
  function queryVisible<T extends HTMLElement>(selector: string): T[] {
    return Array.from(document.querySelectorAll<T>(selector)).filter((el) => el.offsetParent !== null);
  }

  function focusCriterionText(index: number) {
    queryVisible<HTMLElement>(`[aria-label="Criterion ${index + 1} text"]`)[0]?.focus();
  }

  function removeRow(key: string, index: number) {
    setRows((current) => {
      if (!current) return current;
      const next = current.filter((r) => r.key !== key);
      setRemovedAnnouncement(`Criterion removed. ${next.length} remain.`);
      if (next.length === 0) {
        setFocusIntent({ type: "add-criterion" });
      } else if (index < next.length) {
        setFocusIntent({ type: "criterion-text", index });
      } else {
        setFocusIntent({ type: "remove-button", index: next.length - 1 });
      }
      return next;
    });
  }

  function addRow() {
    setRows((current) => {
      const next = [
        ...(current ?? []),
        { key: `new-${++newRowCounter}`, id: null, type: "structural" as CriterionType, text: "", evidence: "", weight: 0 },
      ];
      setFocusIntent({ type: "criterion-text", index: next.length - 1 });
      return next;
    });
  }

  function distributeWeightsEvenly() {
    setRows((current) => {
      if (!current || current.length === 0) return current;
      const evenShare = Math.round((100 / current.length) * 1000) / 1000;
      setRemovedAnnouncement("Weights set equal across all criteria.");
      return current.map((r) => ({ ...r, weight: evenShare }));
    });
  }

  // `isPending` (query still in flight) and `rows === null` (query
  // resolved, local edit state not yet seeded from it) are two distinct
  // waits — collapsing them into one condition made the error branch
  // below unreachable, since a failed query never seeds `rows`.
  if (isPending) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <PageHeader headingRef={headingRef} rubricTitle={undefined} criteriaCount={undefined} />
        <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary">
          <SpinnerIcon />
          Loading criteria.
        </div>
      </div>
    );
  }
  if (isError || !rubric) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <PageHeader headingRef={headingRef} rubricTitle={undefined} criteriaCount={undefined} />
        <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
          Could not load this rubric.{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      </div>
    );
  }
  if (rows === null) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <PageHeader headingRef={headingRef} rubricTitle={rubric.title} criteriaCount={rubric.criteria.length} />
        <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary">
          <SpinnerIcon />
          Loading criteria.
        </div>
      </div>
    );
  }

  // F2.4 (V-013): a version another version has superseded is history —
  // read-only, never silently mutated.
  const readOnly = !rubric.is_latest_version;
  const weightSum = rows.reduce((sum, r) => sum + r.weight, 0);
  const averageWeight = rows.length > 0 ? weightSum / rows.length : 0;
  const showErrors = attempted !== null;

  function blockingProblems(forConfirm: boolean) {
    const problems: { id: string; message: string; focus: () => void }[] = [];
    if (rows!.length === 0) {
      problems.push({
        id: "empty",
        message: "Add at least one criterion.",
        focus: () => addCriterionRef.current?.focus(),
      });
    }
    if (forConfirm) {
      rows!.forEach((r, i) => {
        if (r.text.trim() === "") {
          problems.push({
            id: `text-${r.key}`,
            message: `Criterion ${i + 1} has no text.`,
            focus: () => focusCriterionText(i),
          });
        }
      });
    }
    return problems;
  }

  function toEdits(): CriterionEdit[] {
    return rows!.map((r) => ({
      id: r.id,
      type: r.type,
      text: r.text,
      evidence: r.evidence.trim() ? r.evidence : null,
      weight: r.weight,
    }));
  }

  function attemptSave(confirm: boolean) {
    const problems = blockingProblems(confirm);
    setAttempted(confirm ? "confirm" : "save");
    if (problems.length > 0) {
      setFocusIntent({ type: "summary" });
      return;
    }
    // Snapshot what's actually being submitted now, not `rows` read
    // again inside onSuccess — the instructor could keep typing while
    // the request is in flight, and only the submitted values are
    // actually persisted.
    const submitted = rows!;
    save.mutate(
      { criteria: toEdits(), confirm },
      { onSuccess: () => setLastSavedRows(submitted) },
    );
  }

  const saveError =
    save.error instanceof Error ? save.error.message : save.error ? "Save failed. Try again." : null;
  const currentProblems = showErrors ? blockingProblems(attempted === "confirm") : [];

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <PageHeader headingRef={headingRef} rubricTitle={rubric.title} criteriaCount={rows.length} />

      {readOnly ? (
        <p className="rounded-md border border-border bg-status-neutral-bg px-3 py-2.5 text-sm text-ink-secondary">
          A newer version of this format exists. This version is read-only history: old reports
          stay pinned to it for comparability.
        </p>
      ) : (
        <p className="rounded-md bg-status-info-bg px-3 py-2 text-sm text-status-info-text">
          Nothing runs until you confirm. Re-type, re-weight, delete, or add criteria.
        </p>
      )}

      {rubric.parse_status === "needs_review" && (
        <div role="status" className="rounded-md border border-status-attention-text/20 bg-status-attention-bg px-3 py-2.5 text-sm text-status-attention-text">
          <p className="flex items-center gap-1.5 font-semibold">
            <AttentionIcon />
            Needs manual completion
          </p>
          <p className="mt-1">
            VERIDICAL's parser could not confirm every row below on its own, after several
            attempts. Check each row's criterion text, evidence, and weight against your original
            format file before confirming.
          </p>
          {rubric.parse_issues && rubric.parse_issues.length > 0 && (
            <ul className="mt-1.5 list-disc pl-5">
              {rubric.parse_issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {currentProblems.length > 0 && (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          className="rounded-md border-2 border-status-attention-text/40 bg-status-attention-bg p-3.5 text-sm text-status-attention-text"
        >
          <p className="font-semibold">
            Fix the following before {attempted === "confirm" ? "confirming and activating this rubric" : "saving"}:
          </p>
          <ul className="mt-1.5 list-disc pl-5">
            {currentProblems.map((p) => (
              <li key={p.id}>
                <button type="button" onClick={p.focus} className="text-link underline hover:text-link-hover">
                  {p.message}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Desktop table (>=640px). WCAG 1.4.10's data-table 2D-scroll
          exception is available here but deliberately not used — see the
          mobile card layout below for why.
          V-056: compact density (spacing-2/3, not spacing-4) — the
          diagnosed problem was that the header row promised a scannable
          table while the body delivered full-card padding per row
          (Nielsen #4, an intra-screen inconsistency), which multiplies at
          the 15-20-criterion rubric scale FEATURES.md actually supports
          (Miller's law). The auto-grow textareas are KEPT, not swapped
          for truncate-on-focus: that behavior is real, already-tested
          accessibility work (the resize-race fix above) — tightening the
          row's own padding/gaps closes the density gap without discarding
          it or introducing a new expand/collapse interaction. */}
      <div role="table" aria-label="Rubric criteria" className="hidden max-h-[70vh] overflow-auto rounded-lg border border-border sm:block">
        <div role="row" className="sticky top-0 z-(--z-sticky) grid grid-cols-[28px_minmax(0,1fr)_192px_minmax(0,1fr)_140px_40px] gap-2.5 border-b border-border bg-status-neutral-bg px-3.5 py-2 text-xs font-semibold tracking-header text-ink-tertiary uppercase">
          <span role="columnheader">#</span>
          <span role="columnheader">Criterion</span>
          <span role="columnheader">Type</span>
          <span role="columnheader">Evidence</span>
          <span role="columnheader">Weight</span>
          <span role="columnheader">
            <span className="sr-only">Actions</span>
          </span>
        </div>
        {rows.map((row, index) => {
          const textInvalid = showErrors && row.text.trim() === "";
          return (
            <div
              key={row.key}
              role="row"
              className="grid grid-cols-[28px_minmax(0,1fr)_192px_minmax(0,1fr)_140px_40px] items-start gap-2.5 border-t border-border px-3.5 py-2"
            >
              <span role="cell" className="pt-1.5 text-sm text-ink-tertiary">
                {index + 1}
              </span>
              <span role="cell" className="min-w-0">
                <AutoGrowTextarea
                  value={row.text}
                  onChange={(v) => updateRow(row.key, { text: v })}
                  placeholder="Criterion text"
                  disabled={readOnly}
                  aria-label={`Criterion ${index + 1} text`}
                  aria-invalid={textInvalid ? "true" : undefined}
                  aria-describedby={textInvalid ? `crit-text-err-${row.key}` : undefined}
                  className={cx(
                    "min-h-8 w-full resize-none rounded-md border px-2.5 py-1 text-base text-ink disabled:bg-page disabled:text-ink-tertiary",
                    textInvalid ? "border-2 border-status-attention-text" : "border-border-input",
                  )}
                />
                {textInvalid && (
                  <p id={`crit-text-err-${row.key}`} className="mt-1 text-sm text-status-attention-text">
                    Enter this criterion's text.
                  </p>
                )}
              </span>
              <span role="cell">
                <TypeRadioGroup
                  value={row.type}
                  onChange={(t) => updateRow(row.key, { type: t })}
                  ariaLabel={`Criterion ${index + 1} type`}
                  disabled={readOnly}
                />
              </span>
              <span role="cell" className="min-w-0">
                <AutoGrowTextarea
                  value={row.evidence}
                  onChange={(v) => updateRow(row.key, { evidence: v })}
                  placeholder="What evidence satisfies this?"
                  disabled={readOnly}
                  aria-label={`Criterion ${index + 1} evidence`}
                  className="min-h-8 w-full resize-none rounded-md border border-border-input px-2.5 py-1 text-base text-ink-secondary disabled:bg-page disabled:text-ink-tertiary"
                />
              </span>
              <span role="cell" className="flex flex-col items-start gap-1">
                <input
                  type="text"
                  inputMode="decimal"
                  value={row.weight}
                  disabled={readOnly}
                  aria-label={`Criterion ${index + 1} weight`}
                  onChange={(event) => handleWeightChange(row.key, event.target.value)}
                  className="h-8 w-20 rounded-md border border-border-input px-2 text-base text-ink disabled:bg-page disabled:text-ink-tertiary"
                />
                <WeightImportanceTag importance={liveWeightImportance(row.weight, averageWeight)} />
              </span>
              <span role="cell">
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => removeRow(row.key, index)}
                    aria-label={`Remove criterion ${index + 1}`}
                    className="flex h-8 w-8 items-center justify-center rounded-md text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink"
                  >
                    <RemoveIcon />
                  </button>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {/* Mobile (<640px): card-per-criterion, not table-scroll. Five
          interactive controls per row make this an editing task, not a
          read-only scan — 1.4.10's table exception is available but the
          wrong choice here (same reasoning ManuscriptsTable.tsx already
          applied to its own, simpler read-only list). */}
      <ul className="flex flex-col gap-3 sm:hidden">
        {rows.map((row, index) => {
          const textInvalid = showErrors && row.text.trim() === "";
          return (
            <li key={row.key} className="rounded-lg border border-border bg-panel p-4">
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
                  Criterion {index + 1}
                </span>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => removeRow(row.key, index)}
                    aria-label={`Remove criterion ${index + 1}`}
                    className="flex min-h-11 items-center gap-1 px-2 text-sm font-medium text-status-attention-text"
                  >
                    <RemoveIcon /> Remove
                  </button>
                )}
              </div>
              <label className="mt-2 flex flex-col gap-1">
                <span className="text-sm font-medium text-ink-secondary">Criterion text</span>
                <AutoGrowTextarea
                  value={row.text}
                  onChange={(v) => updateRow(row.key, { text: v })}
                  placeholder="Criterion text"
                  disabled={readOnly}
                  aria-label={`Criterion ${index + 1} text`}
                  aria-invalid={textInvalid ? "true" : undefined}
                  aria-describedby={textInvalid ? `crit-text-err-m-${row.key}` : undefined}
                  className={cx(
                    "min-h-11 w-full resize-none rounded-md border px-3 py-2 text-base text-ink disabled:bg-page disabled:text-ink-tertiary",
                    textInvalid ? "border-2 border-status-attention-text" : "border-border-input",
                  )}
                />
                {textInvalid && (
                  <p id={`crit-text-err-m-${row.key}`} className="text-sm text-status-attention-text">
                    Enter this criterion's text.
                  </p>
                )}
              </label>
              <div className="mt-2 flex flex-col gap-1">
                <span className="text-sm font-medium text-ink-secondary">Type</span>
                <TypeRadioGroup
                  value={row.type}
                  onChange={(t) => updateRow(row.key, { type: t })}
                  ariaLabel={`Criterion ${index + 1} type (mobile)`}
                  fill
                  disabled={readOnly}
                />
              </div>
              <label className="mt-2 flex flex-col gap-1">
                <span className="text-sm font-medium text-ink-secondary">Evidence</span>
                <AutoGrowTextarea
                  value={row.evidence}
                  onChange={(v) => updateRow(row.key, { evidence: v })}
                  placeholder="What evidence satisfies this?"
                  disabled={readOnly}
                  aria-label={`Criterion ${index + 1} evidence (mobile)`}
                  className="min-h-11 w-full resize-none rounded-md border border-border-input px-3 py-2 text-base text-ink-secondary disabled:bg-page disabled:text-ink-tertiary"
                />
              </label>
              <label className="mt-2 flex w-28 flex-col gap-1">
                <span className="text-sm font-medium text-ink-secondary">Weight</span>
                <div className="flex flex-col items-start gap-1">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={row.weight}
                    disabled={readOnly}
                    aria-label={`Criterion ${index + 1} weight (mobile)`}
                    onChange={(event) => handleWeightChange(row.key, event.target.value)}
                    className="h-11 w-20 rounded-md border border-border-input px-2 text-base text-ink disabled:bg-page disabled:text-ink-tertiary"
                  />
                  <WeightImportanceTag importance={liveWeightImportance(row.weight, averageWeight)} />
                </div>
              </label>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="rounded-lg border border-border bg-panel p-4 text-sm text-ink-tertiary">
            No criteria yet.
          </li>
        )}
      </ul>

      <p aria-live="polite" className="sr-only">
        {removedAnnouncement}
      </p>

      {!readOnly && (
        <>
          <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <button
              ref={addCriterionRef}
              type="button"
              onClick={addRow}
              className="flex h-11 items-center gap-1.5 rounded-md px-2 text-sm font-bold text-link hover:underline"
            >
              <PlusIcon /> Add criterion
            </button>
            <span className="hidden flex-1 sm:block" />
            <button
              type="button"
              onClick={distributeWeightsEvenly}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink hover:bg-status-neutral-bg"
            >
              Distribute weights evenly
            </button>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-status-neutral-bg px-2.5 py-1 text-sm font-semibold whitespace-nowrap text-ink-secondary">
              Weight is relative, no required total
            </span>
            <button
              type="button"
              disabled={save.isPending}
              onClick={() => attemptSave(false)}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink disabled:opacity-45"
            >
              Save draft
            </button>
            <button
              type="button"
              data-tour="confirm-rubric-cta"
              disabled={save.isPending}
              onClick={() => attemptSave(true)}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action disabled:opacity-60"
            >
              Confirm &amp; activate rubric
            </button>
          </div>
          <p className="text-sm text-ink-tertiary">Confirming requires every criterion to have text.</p>
        </>
      )}

      {save.isPending && (
        <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-ink-secondary">
          <SpinnerIcon /> Saving.
        </p>
      )}
      {saveError && (
        <p role="alert" className="text-sm font-medium text-status-attention-text">
          <span className="sr-only">Error: </span>
          {saveError}
        </p>
      )}
      {rubric.is_active && (
        <p className="flex items-center gap-1.5 text-sm text-status-success-text">
          <SuccessIcon />
          This rubric is active. Checks use it now.
        </p>
      )}
      <p className="text-sm text-ink-tertiary">
        A re-uploaded format later becomes v2. Old reports stay pinned to their original version.
      </p>

      {blocker.state === "blocked" && (
        <ModalBackdrop>
          <Modal
            title="Leave without saving your changes?"
            onClose={() => resolveBlocker("reset")}
            footer={
              <>
                <button
                  type="button"
                  onClick={() => resolveBlocker("reset")}
                  className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
                >
                  Keep editing
                </button>
                <button
                  type="button"
                  onClick={() => resolveBlocker("proceed")}
                  className="flex h-11 items-center justify-center rounded-md bg-danger px-4 text-sm font-bold text-on-danger hover:bg-danger-hover"
                >
                  Leave without saving
                </button>
              </>
            }
          >
            <p className="text-sm text-ink">
              You have unsaved changes to these criteria. If you leave this page now, those
              changes will be discarded, and the rubric will stay exactly as it was last saved.
            </p>
          </Modal>
        </ModalBackdrop>
      )}
    </div>
  );
}

function PageHeader({
  headingRef,
  rubricTitle,
  criteriaCount,
}: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  rubricTitle: string | undefined;
  criteriaCount: number | undefined;
}) {
  return (
    <header className="flex flex-col gap-1 border-b border-border pb-3 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-ink-tertiary">
          <Link to="/rubric" className="text-link underline hover:text-link-hover">
            Rubric
          </Link>
          <span aria-hidden="true">/</span>
        </nav>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Review parsed criteria
        </h1>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Chip maxWidthClass="max-w-[220px]" title={rubricTitle}>
          {rubricTitle ?? "Loading"}
        </Chip>
        <Chip>
          {criteriaCount === undefined
            ? "…"
            : `${criteriaCount} ${criteriaCount === 1 ? "criterion" : "criteria"}`}
        </Chip>
      </div>
    </header>
  );
}
