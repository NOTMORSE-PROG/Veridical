// Screen 4g — transparent, cancellable check progress (F3.1–F3.6/F8.1).
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import type { CheckRun, CheckRunStatus } from "../api/types";
import { formatManuscriptOption, manuscriptIdentity } from "../domain/manuscriptLabel";
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
  api_down: "An external service is unavailable. This will resume automatically.",
  quota_exhausted: "Daily AI quota reached. This will resume automatically after reset.",
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
  const base = FAILURE_MESSAGES[blocked.code] ?? blocked.message;
  if (!blocked.resume_at) return `${base} This will resume automatically.`;
  return `${base} Resumes at ${formatDateTime(blocked.resume_at)}.`;
}

function degradedDetail(entry: { n_criteria?: number; degraded_count?: number; degraded_code?: string }): string {
  const total = entry.n_criteria ?? 0;
  const degraded = entry.degraded_count ?? 0;
  const graded = total - degraded;
  const reason = DEGRADED_REASONS[entry.degraded_code ?? ""] ?? "an AI service issue";
  return `${degraded} of ${total} criteria were not graded by AI (${reason}). ${graded} ${graded === 1 ? "criterion was" : "criteria were"} graded normally. Review the ungraded criteria directly, or re-run this check after the daily reset.`;
}

type StageState = "attention" | "blocked" | "done" | "pending" | "running" | "skipped";

interface SignalStage {
  id: string;
  label: string;
  state: StageState;
  tagText: string;
  detail?: string;
}

function toStage(run: CheckRun, key: string, label: string): SignalStage {
  const entry = run.stage_status?.stages?.[key];
  const blocked = run.stage_status?.blocked;
  if (run.status === key && blocked) return { id: key, label, state: "blocked", tagText: "Paused", detail: blockedDetail(blocked) };
  if (run.status === key) return { id: key, label, state: "running", tagText: "In progress" };
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
  skipped: "Not run",
};

function manuscriptLabel(run: CheckRun): string {
  if (!run.manuscript_group_label) return `Manuscript #${run.manuscript_id}`;
  return formatManuscriptOption(
    manuscriptIdentity(run.manuscript_group_label, run.manuscript_original_filename),
    run.manuscript_uploaded_at ? formatDateTime(run.manuscript_uploaded_at) : "an earlier date",
  );
}

export function CheckProgressPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: run, isPending, isError, refetch } = useCheckRun(id);
  const cancel = useCancelCheckRun(id);
  const [showCancel, setShowCancel] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Check progress - VERIDICAL", headingRef);

  const prevStatusRef = useRef<CheckRunStatus | undefined>(undefined);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (!run || run.status === prevStatusRef.current) return;
    prevStatusRef.current = run.status;
    if (run.status === "done") setAnnouncement("Check complete. Your readiness report is ready.");
    else if (run.status === "failed") setAnnouncement("This check has failed. See the message below.");
    else if (run.status === "cancelled") setAnnouncement("This check was cancelled.");
    else setAnnouncement(`Now running: ${STAGE_LABEL[run.status] ?? run.status}.`);
  }, [run?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="signal-route signal-page-flow signal-check-progress">
      <header className="signal-route-header signal-check-progress__header">
        <div>
          <nav aria-label="Breadcrumb"><Link to="/dashboard?queue=checking">Review Desk</Link><span aria-hidden="true">/</span><span>Check progress</span></nav>
          <p className="signal-eyebrow">Check · Live pipeline</p>
          <h1 ref={headingRef} tabIndex={-1}>Check progress</h1>
          <p className="signal-route-header__intro">Follow each stage. You can leave this page while the check continues.</p>
        </div>
        {run && <ProcessStatus status={run.status} />}
      </header>

      <p aria-live="polite" className="signal-live-region">{announcement}</p>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="signal-desk-loading"><span>Loading check progress…</span><i /><i /></div>
      ) : isError || !run ? (
        <Alert title="Could not load this check's progress" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert>
      ) : (
        <>
          <section className="signal-check-identity" aria-labelledby="check-manuscript-heading">
            <div><p className="signal-section-kicker">Manuscript</p><h2 id="check-manuscript-heading">{manuscriptLabel(run)}</h2></div>
            <dl><div><dt>Required format</dt><dd>{run.rubric_title ?? "Active format"}</dd></div><div><dt>Run</dt><dd>#{run.id}</dd></div></dl>
          </section>

          {run.status === "queued" && <Alert title="Waiting to start" tone="info" role="status">Queue position {run.queue_position}. VERIDICAL will start this check when an execution slot is available.</Alert>}

          {run.cancel_requested_at && run.status !== "cancelled" && <Alert title="Stopping after the current safe step" tone="warning" role="status">Your cancellation request is recorded. Intermediate work and the audit trail will be kept.</Alert>}

          <section className="signal-check-pipeline" aria-labelledby="pipeline-heading">
            <div className="signal-section-heading"><div><p className="signal-section-kicker">Pipeline</p><h2 id="pipeline-heading">What VERIDICAL is doing</h2></div><p>Completed work remains traceable even if this run stops.</p></div>
            <ol className="signal-check-track">
              {STAGE_ORDER.map(({ key, label }) => toStage(run, key, label)).map((stage) => (
                <li key={stage.id} className={`signal-check-stage signal-check-stage--${stage.state}`} aria-current={stage.state === "running" || stage.state === "blocked" ? "step" : undefined}>
                  <span className="signal-check-stage__mark" aria-hidden="true">{STAGE_MARKS[stage.state]}</span>
                  <div><h3>{stage.label}</h3>{stage.detail && <p>{stage.detail}</p>}</div>
                  <span className="signal-check-stage__state">{stage.tagText}</span>
                </li>
              ))}
            </ol>
          </section>

          {run.status === "failed" && run.stage_status?.failed && <Alert title="This check stopped" tone="error" role="alert">{FAILURE_MESSAGES[run.stage_status.failed.code] ?? run.stage_status.failed.message}</Alert>}

          {run.status === "cancelled" && <Alert title="Check cancelled" tone="info" role="status">No readiness report was produced. The partial run remains in Audit for traceability.</Alert>}

          {cancel.error && <Alert title="Could not cancel this check" tone="error" role="alert">{cancel.error instanceof Error ? cancel.error.message : "Try again."}</Alert>}

          {run.status === "done" && <section className="signal-check-complete" aria-labelledby="check-complete-heading"><div><p className="signal-section-kicker">Next: Review</p><h2 id="check-complete-heading">The readiness report is ready</h2><p>Review every escalation and its evidence before you make a final decision.</p></div><ActionLink to={`/report/${run.id}`} variant="brand">View readiness report</ActionLink></section>}

          <div className="signal-check-actions">
            <ActionLink to="/dashboard?queue=checking" variant="quiet">Back to Review Desk</ActionLink>
            {ACTIVE_STATUSES.has(run.status) && <Button variant="danger" disabled={Boolean(run.cancel_requested_at)} onClick={() => setShowCancel(true)}>{run.cancel_requested_at ? "Cancellation requested" : "Cancel check"}</Button>}
          </div>

          {showCancel && (
            <Dialog
              title="Cancel this check?"
              onClose={() => setShowCancel(false)}
              actions={<><Button variant="secondary" disabled={cancel.isPending} onClick={() => setShowCancel(false)}>Keep checking</Button><Button variant="danger" busy={cancel.isPending} onClick={() => cancel.mutate(undefined, { onSuccess: () => setShowCancel(false) })}>Cancel check</Button></>}
            >
              <div className="signal-dialog-copy"><p>VERIDICAL will stop after the current safe step. It will not create a readiness report.</p><p>Intermediate results and the audit trail are kept; no manuscript file is deleted.</p></div>
            </Dialog>
          )}
        </>
      )}
    </div>
  );
}
