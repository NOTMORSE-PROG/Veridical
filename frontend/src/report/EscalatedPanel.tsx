// "Needs your review" panel (V-023, F3.5, screen 4h): self-consistency
// disagreements the AI never auto-decided (charter rule 1). Resolving is
// the ONLY way one of these becomes a score contribution — never
// automatic.
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { EscalatedItemOut, EscalationResolution } from "../api/types";
import { cx } from "../components/cx";
import { useEscalatedItems, useResolveEscalation } from "./useReport";

// BUG-096: mirrors `config.py`'s `resolution_reason_min_length` -- the
// frontend can't read backend `Settings` per-request (same deliberate,
// disclosed exception D-023 already made for `weight_importance`'s
// ratios), so this is hardcoded here and the two are kept from silently
// drifting by a backend test asserting the default matches
// (`test_report_schemas.py`'s `test_default_minimum_matches_the_
// frontend_hardcoded_copy`). A one-character "x" used to satisfy the old
// `.trim()`-only check and was then published verbatim to the report,
// the PDF, and the public share link. Exported (BUG-095 follow-up) so
// `DecisionModal.tsx` can reuse the same floor for the decision-note
// field instead of hardcoding a second, possibly-drifting copy.
export const RESOLUTION_REASON_MIN_LENGTH = 10;

// Same glyph as StatusPill's "caution" tone (circle + centered exclamation)
// — this panel IS the ambiguous-middle-case surface the caution tone
// exists for: an AI judgment the system could not settle on its own.
function CautionIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="7.5" x2="12" y2="13" />
      <circle cx="12" cy="16.5" r="0.5" fill="currentColor" />
    </svg>
  );
}

// Two different facts land in this panel and must never read alike (V-050):
// the AI graded it and hesitated, or the AI never ran at all. Claiming "no
// votes recorded" for something that was never sent would imply a judgement
// that does not exist. Also distinguishes a genuine split vote (real,
// different opinions) from neither pass producing a verdict at all (V-055:
// found live rendering as "No majority (2 passes)" for both cases, which
// reads as a real disagreement when the actual fact is "no opinion twice").
function voteSummary(item: EscalatedItemOut): string {
  if (item.review_reason === "not_graded") return "Not graded by AI.";
  if (item.votes.length === 0) return "No votes recorded.";
  const real = item.votes.filter((v): v is string => v !== null);
  if (real.length === 0) {
    return `No valid verdict from ${item.votes.length} grading ${item.votes.length === 1 ? "pass" : "passes"}.`;
  }
  if (item.ai_majority_verdict === null) {
    return `Split vote: ${item.votes.map((v) => v ?? "no verdict").join(", ")}.`;
  }
  const agreeing = item.votes.filter((v) => v === item.ai_majority_verdict).length;
  return `Agreement ${agreeing}/${item.votes.length}.`;
}

function ResolveRow({
  item,
  checkRunId,
  onResolved,
}: {
  item: EscalatedItemOut;
  checkRunId: number;
  onResolved: (message: string) => void;
}) {
  const [pending, setPending] = useState<EscalationResolution | null>(null);
  const [reason, setReason] = useState("");
  const [attempted, setAttempted] = useState(false);
  const resolve = useResolveEscalation(checkRunId);

  function start(resolution: EscalationResolution) {
    setPending(resolution);
    setReason("");
    setAttempted(false);
  }

  function confirm() {
    setAttempted(true);
    if (!pending || reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) return;
    resolve.mutate(
      { checkResultId: item.check_result_id, resolution: pending, reason: reason.trim() },
      {
        onSuccess: (out) => {
          setPending(null);
          setAttempted(false);
          // "not_applicable" is what `needs_document` resolves to server-side
          // (excluded from the composite, same as any N/A criterion) -- say
          // that plainly rather than echo the raw outcome enum.
          const outcomeLabel =
            out.outcome === "not_applicable" ? "excluded (needs the document)" : out.outcome;
          onResolved(
            `Resolved as ${outcomeLabel}. Composite score is now ${out.report.composite_score ?? "unavailable"}%.`,
          );
        },
      },
    );
  }

  const serverError =
    resolve.error instanceof ApiError ? resolve.error.message : resolve.error ? "Couldn't resolve this item." : null;
  const reasonInvalid = attempted && reason.trim().length < RESOLUTION_REASON_MIN_LENGTH;
  const reasonErrId = `resolve-reason-err-${item.check_result_id}`;

  return (
    <div className="border-t border-border px-3.5 py-3 text-sm">
      {/* ux-critic finding (V-068): this used to switch to a side-by-side
          row at `sm:` (640px), which reflowed cleanly with 2-3 buttons but
          broke (WCAG 1.4.10) once this ticket's 4th button made the button
          row too wide to share 640-850px with the criterion text -- the
          text column collapsed to ~1 word per line. `lg:` (1024px) is the
          width `ux-critic` confirmed clean; stacking vertically below that
          is the existing, already-safe narrow-viewport layout, not a new
          fallback. */}
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between lg:gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink">"{item.criterion_text}"</p>
          <p className="mt-0.5 text-xs text-ink-tertiary">{voteSummary(item)}</p>
          {item.reason && <p className="mt-0.5 text-xs text-ink-tertiary">{item.reason}</p>}
          {/* V-068 AC1: the charter's 10-second verification bar applied to
              the panel that decides the composite score -- previously this
              row showed nothing the AI actually looked at. Deliberately its
              own block, never merged with a verified `Flag.evidence_excerpt`
              elsewhere in the app (V-068 Q2: verification is what produces a
              real anchor, so there is no anchor to show alongside these). */}
          {item.unverified_evidence && item.unverified_evidence.length > 0 && (
            // `professor` finding (V-068): the ticket's own Q2 research
            // says "the shape itself must carry that distinction, not
            // just the surrounding prose" -- a shared border/background
            // token with the app's normal VERIFIED evidence block
            // (FlagDetail.tsx) didn't meet that bar on a skim. Reuses the
            // panel's own existing caution color (its left accent bar,
            // above) rather than inventing a new token.
            <div
              className="mt-1.5 flex items-start gap-1.5 rounded-md border border-border bg-page p-2"
              style={{ borderLeftWidth: "3px", borderLeftColor: "var(--color-status-caution-text)" }}
            >
              <span className="mt-0.5 shrink-0 text-status-caution-text">
                <CautionIcon />
              </span>
              <div>
                <p className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
                  Could not verify against the source
                </p>
                {item.unverified_evidence.map((quote, i) => (
                  <blockquote key={i} className="mt-1 text-xs break-words text-ink-secondary">
                    "{quote}"
                  </blockquote>
                ))}
              </div>
            </div>
          )}
        </div>
        {pending === null && (
          <div className="flex flex-none flex-wrap gap-2">
            {item.ai_majority_verdict !== null && (
              <button
                type="button"
                onClick={() => start("accept_majority")}
                className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg lg:min-h-9 lg:flex-none lg:px-2.5"
              >
                Accept AI: {item.ai_majority_verdict}
              </button>
            )}
            <button
              type="button"
              onClick={() => start("mark_pass")}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg lg:min-h-9 lg:flex-none lg:px-2.5"
            >
              Pass
            </button>
            <button
              type="button"
              onClick={() => start("mark_fail")}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg lg:min-h-9 lg:flex-none lg:px-2.5"
            >
              Fail
            </button>
            {/* V-068 AC2, DECIDED 2026-08-16: a third option that isn't a
                guess -- excludes this criterion from the composite (like
                not_applicable) and never blocks the decision. */}
            <button
              type="button"
              onClick={() => start("needs_document")}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg lg:min-h-9 lg:flex-none lg:px-2.5"
            >
              Needs the document
            </button>
          </div>
        )}
        {pending === null && item.ai_majority_verdict === null && (
          <p className="text-xs text-ink-tertiary lg:max-w-[220px]">
            The AI reached no verdict to accept for this criterion.
          </p>
        )}
      </div>
      {pending !== null && (
        <div className="mt-2 flex flex-col gap-2 rounded-md border border-border bg-page p-3">
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
              placeholder="Why are you resolving this way?"
              aria-invalid={reasonInvalid ? "true" : undefined}
              aria-describedby={reasonInvalid ? reasonErrId : undefined}
              className={cx(
                "h-11 rounded-md border px-3 text-base text-ink sm:h-9",
                reasonInvalid ? "border-2 border-status-attention-text" : "border-border-input",
              )}
            />
          </label>
          {reasonInvalid && (
            // BUG-069 item 1: this used to sit INSIDE the <label> above --
            // a wrapping <label>'s full text content becomes part of the
            // input's accessible NAME, so every time the field was
            // focused/read, the error was announced a second time as
            // "Reason (required) Enter a reason before confirming, edit
            // text." Moved outside so the name stays just "Reason
            // (required)"; `aria-describedby` still associates it (that
            // doesn't require containment), and `role="alert"` (`ux-critic`
            // finding, WCAG 4.1.3) still announces it immediately on
            // rejection, matching this component's own serverError
            // paragraph below.
            <p id={reasonErrId} role="alert" className="text-sm text-status-attention-text">
              {reason.trim()
                ? `Reason must be at least ${RESOLUTION_REASON_MIN_LENGTH} characters -- this appears in the report, the exported PDF, and any share link.`
                : "Enter a reason before confirming."}
            </p>
          )}
          {serverError && (
            <p role="alert" className="text-sm font-medium text-status-attention-text">
              {serverError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setPending(null)}
              disabled={resolve.isPending}
              className="flex min-h-11 items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink disabled:opacity-60 sm:min-h-9"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirm}
              disabled={resolve.isPending}
              className="flex min-h-11 items-center rounded-md bg-action px-3.5 text-sm font-bold text-on-action disabled:opacity-60 sm:min-h-9"
            >
              {resolve.isPending ? "Saving." : "Confirm"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function EscalatedPanel({ checkRunId }: { checkRunId: number }) {
  const { data: items, isPending } = useEscalatedItems(checkRunId);
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const announcementRef = useRef<HTMLParagraphElement>(null);

  const hasItems = !!items && items.length > 0;

  // V-071 (BUG-054, live-reproduced 3/3 times): focus used to drop to
  // <body> after every escalation resolution, stranding a keyboard user at
  // the top of a document that can run to 34 flags. The heading already
  // carried tabIndex={-1} for exactly this -- nothing had ever called
  // .focus() on it. Called synchronously in the mutation's onSuccess, so
  // the heading is still mounted at the moment this runs even when this
  // was the LAST item (the query hasn't refetched to 0 yet) -- the case
  // that needs more than this is handled by the effect below.
  function handleResolved(message: string) {
    setAnnouncement(message);
    headingRef.current?.focus();
  }

  // ux-critic finding (P1, live-instrumented with a MutationObserver):
  // resolving the LAST escalation used to unmount this whole panel --
  // aria-live region included -- in the same render pass that received
  // the success text, which is a well-documented way for a screen reader
  // to drop the announcement entirely (WAI-ARIA Authoring Practices warn
  // against destroying a live region right after it updates). The
  // announcement paragraph now renders independently of `hasItems` so it
  // survives that transition, and once there's no heading left to hold
  // focus, this effect moves it here instead of letting focus fall back to
  // <body>.
  useEffect(() => {
    if (!hasItems && announcement) announcementRef.current?.focus();
  }, [hasItems, announcement]);

  if (isPending) return null;
  if (!hasItems && !announcement) return null;

  return (
    // Left accent bar carries the "unresolved judgment" signal (V-056) —
    // deliberately NOT a full amber fill like V-055's: an unresolved AI
    // judgment shouldn't look more settled/polished than it is (this
    // product's overreliance-mitigation rule, RESEARCH.md §11), and the
    // Pass/Fail buttons below need to read as active input controls
    // against a plain panel, not get visually swallowed by a tinted one.
    <div
      data-tour="escalated-panel"
      className="overflow-hidden rounded-lg bg-panel"
      style={{ borderLeftWidth: "4px", borderLeftColor: "var(--color-status-caution-text)" }}
    >
      {hasItems && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-status-caution-bg px-3.5 py-2.5">
          <CautionIcon />
          <h2
            id="escalated-heading"
            ref={headingRef}
            tabIndex={-1}
            className="scroll-mt-16 text-sm font-bold text-ink"
          >
            Needs your review ({items!.length})
          </h2>
          <span className="flex-1" />
          <span className="text-xs text-ink-tertiary">
            Escalated by self-consistency vote, never auto-decided (F3.5).
          </span>
        </div>
      )}
      <p ref={announcementRef} tabIndex={-1} aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {hasItems &&
        items!.map((item) => (
          <ResolveRow key={item.check_result_id} item={item} checkRunId={checkRunId} onResolved={handleResolved} />
        ))}
    </div>
  );
}
