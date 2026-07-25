// "Needs your review" panel (V-023, F3.5, screen 4h wireframe id 4h —
// "escalated first"): self-consistency disagreements the AI never
// auto-decided (charter rule 1). Resolving is the ONLY way one of these
// becomes a score contribution — never automatic.
import { useState } from "react";
import { ApiError } from "../api/client";
import type { EscalatedItemOut, EscalationResolution } from "../api/types";
import { useEscalatedItems, useResolveEscalation } from "./useReport";

function agreementLabel(item: EscalatedItemOut): string {
  if (item.votes.length === 0) return "No votes recorded";
  if (item.ai_majority_verdict === null) return `No majority (${item.votes.length} passes)`;
  const agreeing = item.votes.filter((v) => v === item.ai_majority_verdict).length;
  return `Agreement ${agreeing}/${item.votes.length}`;
}

function ResolveRow({ item, checkRunId }: { item: EscalatedItemOut; checkRunId: number }) {
  const [pending, setPending] = useState<EscalationResolution | null>(null);
  const [reason, setReason] = useState("");
  const resolve = useResolveEscalation(checkRunId);

  function start(resolution: EscalationResolution) {
    setPending(resolution);
    setReason("");
  }

  function confirm() {
    if (!pending || !reason.trim()) return;
    resolve.mutate(
      { checkResultId: item.check_result_id, resolution: pending, reason: reason.trim() },
      { onSuccess: () => setPending(null) },
    );
  }

  const serverError =
    resolve.error instanceof ApiError ? resolve.error.message : resolve.error ? "Couldn't resolve this item." : null;

  return (
    <div className="border-t border-border-soft px-3.5 py-2.5 text-xs">
      <div className="flex items-start gap-2.5">
        <div className="flex-1">
          <div className="font-semibold text-ink">"{item.criterion_text}"</div>
          <div className="text-ink-faint">
            {agreementLabel(item)} · Semantic
            {item.reason && <span> · {item.reason}</span>}
          </div>
        </div>
        {pending === null && (
          <div className="flex flex-none gap-1.5">
            <button
              type="button"
              disabled={item.ai_majority_verdict === null}
              title={
                item.ai_majority_verdict === null
                  ? "No AI majority verdict to accept — choose Pass or Fail"
                  : undefined
              }
              onClick={() => start("accept_majority")}
              className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink disabled:opacity-45"
            >
              Accept AI
            </button>
            <button
              type="button"
              onClick={() => start("mark_pass")}
              className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink"
            >
              Pass
            </button>
            <button
              type="button"
              onClick={() => start("mark_fail")}
              className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink"
            >
              Fail
            </button>
          </div>
        )}
      </div>
      {pending !== null && (
        <div className="mt-2 ml-0 flex flex-col gap-1.5 rounded-control border border-border-soft bg-page p-2.5">
          <label className="flex flex-col gap-1">
            <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
              Reason (required)
            </span>
            <input
              type="text"
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you resolving this way?"
              className="rounded-control border border-border-button bg-panel px-2.5 py-1.5 text-xs text-ink"
            />
          </label>
          {serverError && (
            <p role="alert" className="text-2xs font-medium text-status-bad-text">
              {serverError}
            </p>
          )}
          <div className="flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setPending(null)}
              disabled={resolve.isPending}
              className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirm}
              disabled={!reason.trim() || resolve.isPending}
              className="rounded-control border border-primary bg-primary px-2.5 py-1 text-2xs font-medium text-on-primary disabled:opacity-45"
            >
              {resolve.isPending ? "Saving…" : "Confirm"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function EscalatedPanel({ checkRunId }: { checkRunId: number }) {
  const { data: items, isLoading } = useEscalatedItems(checkRunId);

  if (isLoading) return null;
  if (!items || items.length === 0) return null;

  return (
    <div className="rounded-panel border border-border bg-status-warn-bg">
      <div className="flex items-center gap-2 border-b border-border px-3.5 py-2">
        <span className="text-sm font-bold text-ink">Needs your review ({items.length})</span>
        <span className="flex-1" />
        <span className="text-2xs text-ink-faint">
          Escalated by self-consistency vote — never auto-decided (F3.5)
        </span>
      </div>
      {items.map((item) => (
        <ResolveRow key={item.check_result_id} item={item} checkRunId={checkRunId} />
      ))}
    </div>
  );
}
