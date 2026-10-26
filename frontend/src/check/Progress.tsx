import { ReviewDeskReturnLink } from "../routing/ReviewDeskReturnLink";
// Screen 4g — transparent, cancellable check progress (F3.1–F3.6/F8.1).
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import type { CheckRun, CheckRunStatus } from "../api/types";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { ProcessStatus } from "../ui/ProcessStatus";
import { useCancelCheckRun, useCheckRun } from "./useCheckRun";

const STAGE_ORDER = [
  { key: "ingesting", label: "Ingestion" },
  { key: "structural", label: "Structural checks" },
  { key: "semantic", label: "AI grading" },
  { key: "integrity", label: "Integrity checks" },
  { key: "aggregating", label: "Readiness report" },
] as const;

const STAGE_LABEL: Record<string, string> = Object.fromEntries(
  STAGE_ORDER.map(({ key, label }) => [key, label]),
);

const FAILURE_MESSAGES: Record<string, string> = {
  file_malformed: "This manuscript could not be checked because it failed ingestion.",
  api_down: "An external service is unavailable.",
  quota_exhausted: "AI capacity was reached.",
  unexpected_error: "Something went wrong while running this check. Try running it again.",
};

const DEGRADED_REASONS: Record<string, string> = {
  quota_exhausted: "today's free AI capacity was reached",
};

const ACTIVE_STATUSES = new Set<CheckRunStatus>([
  "queued",
  "ingesting",
  "structural",
  "semantic",
  "integrity",
  "aggregating",
]);

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function blockedDetail(blocked: { code: string; message: string; resume_at: string | null }): string {
  const base = `${FAILURE_MESSAGES[blocked.code] ?? blocked.message} This check is paused.`;
  if (!blocked.resume_at) return `${base} No retry time is available. You can leave this page or cancel the check.`;
  return `${base} Eligible to retry after ${formatDateTime(blocked.resume_at)}. Processing may start later.`;
}

type ProgressSummary = {
  kind: "done" | "failed" | "cancelled" | "cancelling" | "blocked" | "queued" | "current";
  title: string;
  announcement: string;
  detail?: string;
};

function progressSummary(run: CheckRun): ProgressSummary {
  if (run.status === "done") return { kind: "done", title: "Check finished", announcement: "Check finished. Open manuscript review to check the results and assessment limits." };
  if (run.status === "failed") return { kind: "failed", title: "Check needs attention", announcement: "This check stopped. Review the error message." };
  if (run.status === "cancelled") return { kind: "cancelled", title: "Check cancelled", announcement: "Check cancelled. No new readiness report was produced." };
  if (run.cancel_requested_at) return { kind: "cancelling", title: "Cancellation requested", announcement: "Cancellation requested. Waiting for the check to stop." };
  const blocked = run.stage_status?.blocked;
  if (blocked) {
    const capacity = blocked.code === "quota_exhausted";
    const detail = blockedDetail(blocked);
    return { kind: "blocked", title: capacity ? "Paused: capacity reached" : "Paused: service unavailable", detail, announcement: `${capacity ? "Check paused because AI capacity was reached." : "Check paused because a service is unavailable."} ${detail}` };
  }
  if (run.status === "queued") return { kind: "queued", title: "Waiting to start", announcement: "Waiting to start. Start order can change across instructors." };
  const title = `Current stage: ${STAGE_LABEL[run.status] ?? run.status}`;
  return { kind: "current", title, announcement: `${title}.` };
}

function degradedDetail(entry: { n_criteria?: number; degraded_count?: number; degraded_code?: string }): string {
  const total = entry.n_criteria ?? 0;
  const degraded = entry.degraded_count ?? 0;
  if (entry.degraded_code === "ai_not_configured") {
    return `${degraded} of ${total} semantic criteria have no completed AI grade because no usable AI configuration was available for this check. AI grading of these ungraded semantic criteria will not retry automatically in this run. Other checks may continue. If a report is produced, review the ungraded criteria in manuscript review and record your own judgments. You can also start a new check after AI configuration is available.`;
  }
  if (entry.degraded_code === "ai_key_rejected") {
    return `${degraded} of ${total} semantic criteria have no completed AI grade because the configured AI key was rejected during an AI request and no applicable alternative completed that request. AI grading of these ungraded semantic criteria will not retry automatically in this run. Other checks may continue. If a report is produced, review the ungraded criteria in manuscript review and record your own judgments. You can also start a new check after AI access is restored.`;
  }
  if (entry.degraded_code === "api_down") {
    return `${degraded} of ${total} semantic criteria have no completed AI grade because an AI service was unavailable. AI grading of these ungraded semantic criteria will not retry automatically in this run. Other checks may continue. If a report is produced, review the ungraded criteria in manuscript review and record your own judgments. You can also start a new check later.`;
  }
  const reason = DEGRADED_REASONS[entry.degraded_code ?? ""] ?? "an AI service issue";
  const recovery = entry.degraded_code === "quota_exhausted"
    ? "Review the ungraded criteria directly, or start a new check after the daily reset."
    : "Review the ungraded criteria directly, or start a new check later.";
  return `${degraded} of ${total} criteria were not graded by AI (${reason}). ${recovery}`;
}

type StageState = "attention" | "blocked" | "done" | "pending" | "running" | "skipped";

interface SignalStage {
  id: string;
  label: string;
  state: StageState;
  tagText: string;
  detail?: string;
}

function toStage(run: CheckRun, key: string, label: string, summary: ProgressSummary): SignalStage {
  const entry = run.stage_status?.stages?.[key];
  if (run.status === key && summary.kind === "cancelling") return { id: key, label, state: "blocked", tagText: "Cancellation requested" };
  if (run.status === key && summary.kind === "blocked") return { id: key, label, state: "blocked", tagText: "Paused", detail: summary.detail };
  if (run.status === key) return { id: key, label, state: "running", tagText: "Current stage" };
  if (entry?.status === "skipped") return { id: key, label, state: "skipped", tagText: "Skipped", detail: typeof entry.note === "string" ? entry.note : undefined };
  if (entry?.status === "done" && (entry.degraded_count ?? 0) > 0) return { id: key, label, state: "attention", tagText: "Needs review", detail: degradedDetail(entry) };
  if (entry?.status === "done") return { id: key, label, state: "done", tagText: "Done" };
  return { id: key, label, state: "pending", tagText: "Not started yet" };
}

const STAGE_MARKS: Record<StageState, string> = {
  attention: "!",
  blocked: "Ⅱ",
  done: "✓",
  pending: "·",
  running: "→",
  skipped: "-",
};

function manuscriptLabel(run: CheckRun): string {
  return manuscriptIdentity(run.manuscript_group_label ?? `Manuscript #${run.manuscript_id}`, run.manuscript_original_filename).primary;
}

export function CheckProgressPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: run, isPending, isError, refetch } = useCheckRun(id);
  const cancel = useCancelCheckRun(id);
  const [showCancel, setShowCancel] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const cancelCloseRef = useRef<HTMLButtonElement>(null);
  const cancellationReturnRun = useRef<number | null>(null);
  useRouteFocus("Check progress - VERIDICAL", headingRef);

  const summary = run ? progressSummary(run) : undefined;
  const nextAnnouncement = run && summary && !isPending && !isError ? `Check #${run.id}. ${summary.announcement}` : "";
  const [announcement, setAnnouncement] = useState("");
  const cancellationError = cancel.error
    ? <Alert title="Could not cancel this check" tone="error" role="alert">{cancel.error instanceof Error ? cancel.error.message : "Try again."}</Alert>
    : null;
  useEffect(() => {
    setAnnouncement(nextAnnouncement);
  }, [nextAnnouncement]);

  return (
    <div className="signal-route signal-page-flow signal-check-progress">
      <header className="signal-route-header signal-check-progress__header">
        <div>
          <nav aria-label="Breadcrumb"><ReviewDeskReturnLink fallback="/dashboard?queue=checking">Review Desk</ReviewDeskReturnLink><span aria-hidden="true">/</span><span>Check progress</span></nav>
          <p className="signal-eyebrow">Manuscript check</p>
          <h1 ref={headingRef} tabIndex={-1}>Check progress</h1>
          <p className="signal-route-header__intro">Follow the recorded stages. Leaving this page does not cancel the check.</p>
        </div>
        {run && summary && (summary.kind === "current" || summary.kind === "blocked" || summary.kind === "cancelling"
          ? <span className={`signal-process-status signal-process-status--${summary.kind === "current" ? "running" : "attention"}`}>{summary.title}</span>
          : <ProcessStatus status={run.status} />)}
      </header>

      <p role="status" aria-atomic="true" className="signal-live-region">{announcement}</p>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading check progress…</span><i /><i /></div>
      ) : isError || !run ? (
        <Alert title="Could not load this check's progress" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>


          {summary?.kind === "queued" && <Alert title="Waiting to start" tone="info">Your check is saved and waiting to start. Checks share capacity across instructors, so start order can change. A start time is not available. You can leave this page and return later.</Alert>}

          {summary?.kind === "cancelling" && <Alert title="Cancellation requested" tone="warning">Your cancellation request is recorded. Waiting for the check to stop. Intermediate work and the audit trail will be kept.</Alert>}

          {summary?.kind === "blocked" && run.status === "queued" && <Alert title={summary.title} tone="warning">{summary.detail}</Alert>}

          <div className="signal-check-layout">
          <section className="signal-check-pipeline" aria-labelledby="pipeline-heading">
            <div className="signal-section-heading"><div><p className="signal-section-kicker">Check stages</p><h2 id="pipeline-heading">Recorded progress</h2></div><p>Completed work remains traceable even if this run stops.</p></div>
            <ol className="signal-check-track">
              {STAGE_ORDER.map(({ key, label }) => toStage(run, key, label, summary!)).map((stage) => (
                <li key={stage.id} className={`signal-check-stage signal-check-stage--${stage.state}`} aria-current={stage.state === "running" || stage.state === "blocked" ? "step" : undefined}>
                  <span className="signal-check-stage__mark" aria-hidden="true">{STAGE_MARKS[stage.state]}</span>
                  <div><h3>{stage.label}</h3>{stage.detail && <p>{stage.detail}</p>}</div>
                  <span className="signal-check-stage__state">{stage.tagText}</span>
                </li>
              ))}
            </ol>
          </section>
          <div className="signal-check-context">
          <section className="signal-check-identity" aria-labelledby="check-manuscript-heading">
            <div><p className="signal-section-kicker">Manuscript</p><h2 id="check-manuscript-heading">{manuscriptLabel(run)}</h2><p className="signal-check-filename">{run.manuscript_original_filename ?? "Filename unavailable"}</p></div>
            <dl><div><dt>Uploaded</dt><dd>{run.manuscript_uploaded_at ? formatDateTime(run.manuscript_uploaded_at) : "Earlier upload"}</dd></div><div><dt>Required format</dt><dd>{run.rubric_title ?? "Active format"}</dd></div><div><dt>Run</dt><dd>#{run.id}</dd></div></dl>
          </section>
          {run.status === "done" && <section className="signal-check-complete" aria-labelledby="check-complete-heading"><div><p className="signal-section-kicker">Next: Review</p><h2 id="check-complete-heading">Review available</h2><p>Read the manuscript beside VERIDICAL's recorded criteria and integrity signals before you make a final decision.</p></div><ActionLink to={`/report/${run.id}/document`} variant="brand">Open manuscript review</ActionLink></section>}
          </div>
          </div>

          {run.status === "failed" && run.stage_status?.failed && <Alert title="This check stopped" tone="error">{FAILURE_MESSAGES[run.stage_status.failed.code] ?? run.stage_status.failed.message}</Alert>}

          {run.status === "cancelled" && <Alert title="Check cancelled" tone="info">No new readiness report was produced. The partial run remains in Audit for traceability.</Alert>}

          {!showCancel && cancellationError}



          <div className="signal-check-actions">
            <ReviewDeskReturnLink fallback="/dashboard?queue=checking" variant="quiet">Back to Review Desk</ReviewDeskReturnLink>
            {ACTIVE_STATUSES.has(run.status) && <Button variant="danger" disabled={Boolean(run.cancel_requested_at)} onClick={() => {
              if (!cancel.isPending || cancellationReturnRun.current !== id) cancellationReturnRun.current = null;
              setShowCancel(true);
            }}>{run.cancel_requested_at ? "Cancellation requested" : "Cancel check"}</Button>}
          </div>

          {showCancel && (
            <Dialog
              title="Cancel this check?"
              onClose={() => setShowCancel(false)}
              returnFocusTarget={() => cancellationReturnRun.current === id ? headingRef.current : null}
              fallbackFocusRef={headingRef}
              closeButtonRef={cancelCloseRef}
              actions={<><Button variant="secondary" disabled={cancel.isPending} onClick={() => setShowCancel(false)}>Keep checking</Button><Button variant="danger" busy={cancel.isPending} onClick={(event) => {
                cancellationReturnRun.current = id;
                // The submitting action becomes natively disabled while busy.
                // Hand focus to a persistent dialog control before that commit.
                const dialog = event.currentTarget.closest("[role='dialog']");
                const close = cancelCloseRef.current;
                if (document.hasFocus() && dialog?.contains(document.activeElement)
                  && close?.isConnected && !close.disabled) close.focus();
                cancel.mutate(undefined, {
                  onSuccess: () => { if (cancellationReturnRun.current === id) setShowCancel(false); },
                  onError: () => { if (cancellationReturnRun.current === id) cancellationReturnRun.current = null; },
                });
              }}>Cancel check</Button></>}
            >
              <div className="signal-dialog-copy"><p>VERIDICAL will stop after the current safe step. It will not create a readiness report.</p><p>Intermediate results and the audit trail are kept; no manuscript file is deleted.</p></div>
              {cancellationError}
            </Dialog>
          )}
        </>
      )}
    </div>
  );
}
