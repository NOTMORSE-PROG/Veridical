// Screen 4i — Flag evidence (F8.2/F8.4): evidence excerpt + provenance,
// free-text annotation, and an AI-verdict override with a MANDATORY
// reason. The original AI/check finding is never destroyed by an
// override — both are always shown side by side (ticket AC).
import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { ApiError } from "../api/client";
import { Tag, type Severity } from "../components/Tag";
import { useAnnotateFlag, useFlag, useOverrideFlag } from "./useFlag";

const SEVERITY_MAP: Record<string, Severity> = { high: "high", med: "medium", low: "low" };

function AnnotationBox({ flagId, initial }: { flagId: number; initial: string | null }) {
  const [value, setValue] = useState(initial ?? "");
  const [saved, setSaved] = useState(false);
  const annotate = useAnnotateFlag(flagId);

  useEffect(() => {
    setValue(initial ?? "");
  }, [initial]);

  function save() {
    if (!value.trim()) return;
    annotate.mutate(value.trim(), { onSuccess: () => setSaved(true) });
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className="flex flex-col gap-1">
        <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
          Annotation — visible on the shared adviser report
        </span>
        <textarea
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
          }}
          rows={2}
          className="rounded-control border border-border-button bg-panel px-2.5 py-1.5 text-xs text-ink"
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={!value.trim() || annotate.isPending}
          className="w-fit rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink disabled:opacity-45"
        >
          {annotate.isPending ? "Saving…" : "Save annotation"}
        </button>
        {saved && !annotate.isPending && (
          <span className="text-2xs text-status-ok-text">Saved.</span>
        )}
      </div>
    </div>
  );
}

function OverrideControl({ flagId }: { flagId: number }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const override = useOverrideFlag(flagId);

  const serverError =
    override.error instanceof ApiError
      ? override.error.message
      : override.error
        ? "Couldn't override this flag."
        : null;

  if (!open) {
    return (
      <div className="flex gap-2">
        <button
          type="button"
          disabled
          title="Accepting the AI verdict as-is needs no action — it's already what the report shows"
          className="w-fit rounded-control border border-primary bg-primary px-2.5 py-1 text-2xs font-medium text-on-primary opacity-45"
        >
          Accept AI verdict
        </button>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="w-fit rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink"
        >
          Override
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5 rounded-control border border-border-soft bg-page p-2.5">
      <label className="flex flex-col gap-1">
        <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
          Reason (required)
        </span>
        <input
          type="text"
          autoFocus
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why are you overriding this finding?"
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
          onClick={() => setOpen(false)}
          disabled={override.isPending}
          className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-2xs font-medium text-ink disabled:opacity-60"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => reason.trim() && override.mutate(reason.trim(), { onSuccess: () => setOpen(false) })}
          disabled={!reason.trim() || override.isPending}
          className="rounded-control border border-primary bg-primary px-2.5 py-1 text-2xs font-medium text-on-primary disabled:opacity-45"
        >
          {override.isPending ? "Saving…" : "Confirm override"}
        </button>
      </div>
    </div>
  );
}

export function FlagDetailPage() {
  const { flagId } = useParams<{ flagId: string }>();
  const id = Number(flagId);
  const { data: flag, isLoading, error } = useFlag(id);

  if (isLoading) {
    return <div className="p-4 text-xs text-ink-faint">Loading flag…</div>;
  }
  if (error) {
    const message = error instanceof ApiError ? error.message : "This flag couldn't be loaded.";
    return (
      <div className="p-4 text-xs text-status-bad-text" role="alert">
        {message}
      </div>
    );
  }
  if (!flag) return null;

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="border-b border-border-soft pb-2">
        <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
          {flag.check_kind.replace(/_/g, " ")} · Flag {flag.id}
        </span>
        <div className="mt-1 flex items-center gap-2">
          <span className="text-md font-bold text-ink">
            {flag.criterion_text ?? "Integrity finding"}
          </span>
          <Tag severity={SEVERITY_MAP[flag.severity] ?? "low"}>{flag.severity.toUpperCase()}</Tag>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {flag.ai_verdict_summary && (
          <span className="rounded-pill border border-modal-border bg-panel px-2.5 py-0.5 text-2xs text-status-bad-text">
            AI verdict: {flag.ai_verdict_summary}
          </span>
        )}
        {flag.confidence !== null && (
          <span className="rounded-pill border border-modal-border bg-panel px-2.5 py-0.5 text-2xs text-ink-soft">
            Confidence {flag.confidence.toFixed(2)}
          </span>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
            Evidence
          </span>
          <span className="text-2xs text-ink-faint">{flag.page_anchor}</span>
        </div>
        <p className="rounded-control border border-border-soft bg-page px-3 py-2 text-xs text-ink italic">
          "{flag.evidence_excerpt}"
        </p>
        {flag.ai_reasoning && <p className="text-xs text-ink-soft">{flag.ai_reasoning}</p>}
      </div>

      {flag.overridden ? (
        <p className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text">
          AI said <b>{flag.ai_verdict_summary ?? "—"}</b> · Instructor overrode this finding —{" "}
          {flag.override_reason}
        </p>
      ) : (
        <OverrideControl flagId={flag.id} />
      )}

      <AnnotationBox flagId={flag.id} initial={flag.annotation} />
    </div>
  );
}
