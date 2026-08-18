// "Needs your review" panel (V-023, F3.5, screen 4h): self-consistency
// disagreements the AI never auto-decided (charter rule 1). Resolving is
// the ONLY way one of these becomes a score contribution — never
// automatic.
import { useState } from "react";
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
          onResolved(
            `Resolved as ${out.outcome}. Composite score is now ${out.report.composite_score ?? "unavailable"}%.`,
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
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink">"{item.criterion_text}"</p>
          <p className="mt-0.5 text-xs text-ink-tertiary">{voteSummary(item)}</p>
          {item.reason && <p className="mt-0.5 text-xs text-ink-tertiary">{item.reason}</p>}
        </div>
        {pending === null && (
          <div className="flex flex-none flex-wrap gap-2">
            {item.ai_majority_verdict !== null && (
              <button
                type="button"
                onClick={() => start("accept_majority")}
                className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg sm:min-h-9 sm:flex-none sm:px-2.5"
              >
                Accept AI: {item.ai_majority_verdict}
              </button>
            )}
            <button
              type="button"
              onClick={() => start("mark_pass")}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg sm:min-h-9 sm:flex-none sm:px-2.5"
            >
              Pass
            </button>
            <button
              type="button"
              onClick={() => start("mark_fail")}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg sm:min-h-9 sm:flex-none sm:px-2.5"
            >
              Fail
            </button>
          </div>
        )}
        {pending === null && item.ai_majority_verdict === null && (
          <p className="text-xs text-ink-tertiary sm:max-w-[220px]">
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
            {reasonInvalid && (
              <p id={reasonErrId} className="text-sm text-status-attention-text">
                {reason.trim()
                  ? `Reason must be at least ${RESOLUTION_REASON_MIN_LENGTH} characters -- this appears in the report, the exported PDF, and any share link.`
                  : "Enter a reason before confirming."}
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

  if (isPending) return null;
  if (!items || items.length === 0) return null;

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
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-status-caution-bg px-3.5 py-2.5">
        <CautionIcon />
        <h2 id="escalated-heading" tabIndex={-1} className="scroll-mt-16 text-sm font-bold text-ink">
          Needs your review ({items.length})
        </h2>
        <span className="flex-1" />
        <span className="text-xs text-ink-tertiary">
          Escalated by self-consistency vote, never auto-decided (F3.5).
        </span>
      </div>
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {items.map((item) => (
        <ResolveRow key={item.check_result_id} item={item} checkRunId={checkRunId} onResolved={setAnnouncement} />
      ))}
    </div>
  );
}
