// Screen 4i — Flag evidence (F8.2/F8.4): evidence excerpt + provenance,
// free-text annotation, and an AI-verdict override with a MANDATORY
// reason. The original AI/check finding is never destroyed by an
// override — both are always shown side by side (ticket AC).
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { FlagOut } from "../api/types";
import { AnchorPill } from "../components/AnchorPill";
import { Chip } from "../components/Chip";
import { cx } from "../components/cx";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { checkKindMeta, humanize } from "../domain/checkKind";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useAnnotateFlag, useFlag, useOverrideFlag } from "./useFlag";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function AnnotationBox({ flagId, initial }: { flagId: number; initial: string | null }) {
  const [value, setValue] = useState(initial ?? "");
  const [attempted, setAttempted] = useState(false);
  const [saved, setSaved] = useState(false);
  const annotate = useAnnotateFlag(flagId);

  useEffect(() => {
    setValue(initial ?? "");
  }, [initial]);

  function save() {
    setAttempted(true);
    setSaved(false);
    if (!value.trim()) return;
    annotate.mutate(value.trim(), { onSuccess: () => setSaved(true) });
  }

  const invalid = attempted && !value.trim();

  return (
    <section aria-label="Annotation" className="flex flex-col gap-1.5">
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium text-ink">Annotation</span>
        <span className="text-xs text-ink-tertiary">
          Private to your account. Not included in a shared report yet.
        </span>
        <textarea
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
            if (attempted) setAttempted(false);
          }}
          rows={2}
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={invalid ? "annotation-err" : undefined}
          className={cx(
            "rounded-md border px-3 py-2 text-base text-ink",
            invalid ? "border-2 border-status-attention-text" : "border-border-input",
          )}
        />
        {invalid && (
          <p id="annotation-err" className="text-sm text-status-attention-text">
            Enter a note before saving.
          </p>
        )}
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={annotate.isPending}
          className="flex min-h-11 w-fit items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink disabled:opacity-60 sm:min-h-9"
        >
          {annotate.isPending ? "Saving." : "Save annotation"}
        </button>
        {saved && !annotate.isPending && (
          <span role="status" aria-live="polite" className="text-sm text-status-success-text">
            Saved.
          </span>
        )}
      </div>
    </section>
  );
}

function OverrideControl({ flagId }: { flagId: number }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const override = useOverrideFlag(flagId);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const wasOpenRef = useRef(false);

  // Cancel doesn't unmount this component (unlike a successful override,
  // which the parent handles separately by swapping in the terminal
  // banner) — so a plain state-transition effect is enough to return
  // focus to the button that reopens the form, instead of leaving it
  // stranded on <body> (ux-critic finding, V-055 4i review).
  useEffect(() => {
    if (wasOpenRef.current && !open) {
      triggerRef.current?.focus();
    }
    wasOpenRef.current = open;
  }, [open]);

  const serverError =
    override.error instanceof ApiError
      ? override.error.message
      : override.error
        ? "Couldn't override this flag."
        : null;

  function start() {
    setOpen(true);
    setReason("");
    setAttempted(false);
  }

  function confirm() {
    setAttempted(true);
    if (!reason.trim()) return;
    override.mutate(reason.trim(), { onSuccess: () => setOpen(false) });
  }

  const invalid = attempted && !reason.trim();

  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-ink-secondary">
          This finding stands as VERIDICAL reported it unless you override it below.
        </p>
        <button
          ref={triggerRef}
          type="button"
          onClick={start}
          className="flex min-h-11 w-fit items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink hover:bg-status-neutral-bg sm:min-h-9"
        >
          Override
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-page p-3">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
          Reason (required)
        </span>
        <input
          type="text"
          autoFocus
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            if (attempted) setAttempted(false);
          }}
          placeholder="Why are you overriding this finding?"
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={invalid ? "override-reason-err" : undefined}
          className={cx(
            "h-11 rounded-md border px-3 text-base text-ink sm:h-9",
            invalid ? "border-2 border-status-attention-text" : "border-border-input",
          )}
        />
        {invalid && (
          <p id="override-reason-err" className="text-sm text-status-attention-text">
            Enter a reason before confirming.
          </p>
        )}
      </label>
      {serverError && (
        <p role="alert" className="text-sm font-medium text-status-attention-text">
          {serverError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={override.isPending}
          className="flex min-h-11 items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink disabled:opacity-60 sm:min-h-9"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={confirm}
          disabled={override.isPending}
          className="flex min-h-11 items-center rounded-md bg-action px-3.5 text-sm font-bold text-on-action disabled:opacity-60 sm:min-h-9"
        >
          {override.isPending ? "Saving." : "Confirm override"}
        </button>
      </div>
    </div>
  );
}

function humanizedVerdict(flag: FlagOut): string {
  return flag.ai_verdict_summary ? humanize(flag.ai_verdict_summary) : "unavailable";
}

export function FlagDetailPage() {
  const { flagId } = useParams<{ flagId: string }>();
  const id = Number(flagId);
  const { data: flag, isPending, isError, error, refetch } = useFlag(id);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Flag - VERIDICAL", headingRef);

  // OverrideControl unmounts entirely once `flag.overridden` flips true
  // (the parent swaps it for this banner) — so the "where did focus go"
  // question has to be answered here, not inside the now-gone control.
  // Same transition-tracking shape as 4g/4h's own status-change effects.
  const bannerRef = useRef<HTMLDivElement>(null);
  const wasOverriddenRef = useRef(false);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (!flag) return;
    if (flag.overridden && !wasOverriddenRef.current) {
      bannerRef.current?.focus();
      setAnnouncement("This finding was overridden. The readiness report was recalculated.");
    }
    wasOverriddenRef.current = flag.overridden;
    // Deliberately keyed on the boolean, not the whole `flag` object
    // (a new identity every refetch) — same shape as Progress.tsx's
    // status-transition effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flag?.overridden]);

  const meta = flag ? checkKindMeta(flag.check_kind) : null;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-col gap-2 border-b border-border pb-3">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-ink-tertiary">
          <Link to="/dashboard" className="text-link underline hover:text-link-hover">
            Dashboard
          </Link>
          <span aria-hidden="true">/</span>
          {flag && (
            <>
              <Link to={`/report/${flag.check_run_id}`} className="text-link underline hover:text-link-hover">
                {flag.manuscript_group_label}
              </Link>
              <span aria-hidden="true">/</span>
            </>
          )}
          <span aria-current="page">Flag</span>
        </nav>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-1">
            {meta && (
              <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
                {meta.eyebrow}
              </span>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
                {meta ? meta.title : "Flag"}
              </h1>
              {flag && <SeverityTag severity={flag.severity as Severity} />}
            </div>
            {flag?.criterion_text && (
              <p className="text-sm text-ink-tertiary">Related criterion: {flag.criterion_text}</p>
            )}
          </div>
          {flag && <span className="text-xs text-ink-tertiary tabular-nums">#{flag.id}</span>}
        </div>
      </header>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary">
          <SpinnerIcon />
          Loading flag.
        </div>
      ) : isError || !flag ? (
        <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
          {error instanceof ApiError ? error.message : "This flag couldn't be loaded."}{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      ) : (
        <>
          <p aria-live="polite" className="sr-only">
            {announcement}
          </p>

          <section aria-label="AI finding" className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-1.5">
              <Chip maxWidthClass="max-w-[280px]" title={`AI verdict: ${humanizedVerdict(flag)}`}>
                AI verdict: {humanizedVerdict(flag)}
              </Chip>
              {flag.confidence !== null && <Chip>Agreement {Math.round(flag.confidence * 100)}%</Chip>}
            </div>
            {flag.confidence !== null && (
              <p className="text-xs text-ink-tertiary">
                How consistently VERIDICAL's repeated AI passes agreed on this finding.
              </p>
            )}
          </section>

          <section aria-label="Evidence" className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Evidence</span>
            <blockquote className="rounded-lg border border-border bg-page px-4 py-3 text-sm break-words text-ink">
              {flag.evidence_excerpt}
            </blockquote>
            <AnchorPill anchor={flag.page_anchor} />
            {flag.ai_reasoning && <p className="text-sm text-ink-secondary">{flag.ai_reasoning}</p>}
          </section>

          {flag.overridden ? (
            <div
              ref={bannerRef}
              tabIndex={-1}
              className="flex flex-col gap-1.5 rounded-lg bg-status-info-bg px-4 py-3 text-sm text-status-info-text"
            >
              <p>
                AI said: <b>{humanizedVerdict(flag)}</b>. You overrode this finding.
              </p>
              <p>Reason: {flag.override_reason}</p>
              <p className="text-xs">
                The readiness report was recalculated.{" "}
                <Link to={`/report/${flag.check_run_id}`} className="font-medium underline">
                  View updated report
                </Link>
                .
              </p>
            </div>
          ) : (
            <OverrideControl flagId={flag.id} />
          )}

          <AnnotationBox flagId={flag.id} initial={flag.annotation} />
        </>
      )}
    </div>
  );
}
