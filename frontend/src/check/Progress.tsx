// Screen 4g — Check progress, per-stage pipeline (ENGINEERING.md §4).
// V2 implements 5 of the wireframe's 8 stages (ingestion, structural,
// semantic, integrity, aggregating) — the other integrity sub-rows
// (citation/statistics/agreement/originality) arrive in V4/V5 and are
// honestly collapsed into ONE "Integrity checks" row marked skipped,
// never faked as passed (charter rule 9).
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import type { CheckRun, CheckRunStatus } from "../api/types";
import { Stepper, type Step, type StepState } from "../components/Stepper";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useCheckRun } from "./useCheckRun";

const STAGE_ORDER: { key: string; label: string }[] = [
  { key: "ingesting", label: "Ingestion" },
  { key: "structural", label: "Structural checks" },
  { key: "semantic", label: "AI grading" },
  { key: "integrity", label: "Integrity checks" },
  { key: "aggregating", label: "Readiness report" },
];

const STAGE_LABEL: Record<string, string> = Object.fromEntries(
  STAGE_ORDER.map(({ key, label }) => [key, label]),
);

const FAILURE_MESSAGES: Record<string, string> = {
  file_malformed: "This manuscript could not be checked because it failed ingestion.",
  api_down: "An external service is unavailable. This will resume automatically.",
  quota_exhausted: "Daily AI quota reached. This will resume automatically after reset.",
};

const DEGRADED_REASONS: Record<string, string> = {
  quota_exhausted: "today's free AI capacity was reached",
};

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
  return (
    `${degraded} of ${total} criteria were not graded by AI (${reason}). ` +
    `${graded} ${graded === 1 ? "criterion was" : "criteria were"} graded normally. ` +
    "Review the ungraded criteria directly, or re-run this check after the daily reset."
  );
}

function toStep(run: CheckRun, key: string, label: string): Step {
  const entry = run.stage_status?.stages?.[key];
  const blocked = run.stage_status?.blocked;

  if (run.status === key && blocked) {
    return { id: key, label, state: "blocked", tagText: "Paused", detail: blockedDetail(blocked) };
  }
  if (run.status === key) {
    return { id: key, label, state: "running", tagText: "In progress" };
  }
  if (entry?.status === "skipped") {
    return {
      id: key,
      label,
      state: "skipped",
      tagText: "Skipped",
      detail: typeof entry.note === "string" ? entry.note : undefined,
    };
  }
  if (entry?.status === "done" && (entry.degraded_count ?? 0) > 0) {
    return { id: key, label, state: "attention", tagText: "Needs review", detail: degradedDetail(entry) };
  }
  if (entry?.status === "done") {
    return { id: key, label, state: "done", tagText: "Done" };
  }
  return { id: key, label, state: "pending" as StepState, tagText: "Not started yet" };
}

export function CheckProgressPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: run, isPending, isError, refetch } = useCheckRun(id);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Check progress - VERIDICAL", headingRef);

  // Announce only on a genuine status transition — `[run?.status]` (a
  // string) rather than `[run]` (a new object identity every 2s poll
  // tick even when nothing changed) is what keeps a live-updating page
  // from re-announcing the same state on every poll.
  const prevStatusRef = useRef<CheckRunStatus | undefined>(undefined);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (!run || run.status === prevStatusRef.current) return;
    prevStatusRef.current = run.status;
    if (run.status === "done") {
      setAnnouncement("Check complete. Your readiness report is ready.");
    } else if (run.status === "failed") {
      setAnnouncement("This check has failed. See the message below.");
    } else {
      setAnnouncement(`Now running: ${STAGE_LABEL[run.status] ?? run.status}.`);
    }
    // Deliberately depends on run?.status (a primitive), not run (a new
    // object identity every 2s poll tick) — see the comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status]);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-col gap-1">
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Check progress
        </h1>
        {run && (
          <p className="text-sm text-ink-secondary">
            {run.manuscript_group_label
              ? `${run.manuscript_group_label}, uploaded ${run.manuscript_uploaded_at ? formatDateTime(run.manuscript_uploaded_at) : "an earlier date"}`
              : `Manuscript #${run.manuscript_id}`}{" "}
            against {run.rubric_title ?? "the active rubric"}
          </p>
        )}
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-tertiary">
          Loading check progress.
        </div>
      ) : isError || !run ? (
        <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
          Could not load this check's progress.{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      ) : (
        <>
          {run.status === "queued" && (
            <span className="inline-flex w-fit items-center rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-secondary">
              Waiting to start. Queue position {run.queue_position}.
            </span>
          )}

          <Stepper steps={STAGE_ORDER.map(({ key, label }) => toStep(run, key, label))} />

          {run.status === "failed" && run.stage_status?.failed && (
            <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
              {FAILURE_MESSAGES[run.stage_status.failed.code] ?? run.stage_status.failed.message}
            </div>
          )}

          {run.status === "done" && (
            <Link
              to={`/report/${run.id}`}
              className="flex h-11 w-fit items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
            >
              View readiness report
            </Link>
          )}
        </>
      )}
    </div>
  );
}
