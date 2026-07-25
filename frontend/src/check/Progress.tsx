// Screen 4g — Check progress, per-stage pipeline (ENGINEERING.md §4).
// V2 implements 5 of the wireframe's 8 stages (ingestion, structural,
// semantic, integrity, aggregating) — the other integrity sub-rows
// (citation/statistics/agreement/originality) arrive in V4/V5 and are
// honestly collapsed into ONE "Integrity checks" row marked skipped,
// never faked as passed (charter rule 9).
import { Link, useParams } from "react-router";
import { Stepper, type Step, type StepState } from "../components/Stepper";
import type { CheckRun } from "../api/types";
import { useCheckRun } from "./useCheckRun";

const STAGE_ORDER: { key: string; label: string }[] = [
  { key: "ingesting", label: "Ingestion" },
  { key: "structural", label: "Structural checks" },
  { key: "semantic", label: "AI grading" },
  { key: "integrity", label: "Integrity checks" },
  { key: "aggregating", label: "Readiness report" },
];

function stageState(run: CheckRun, key: string): StepState {
  const entry = run.stage_status?.stages?.[key];
  if (entry?.status === "done" || entry?.status === "skipped") return "done";
  if (run.status === key) return "running";
  return "pending";
}

function stageNote(run: CheckRun, key: string): string | undefined {
  const entry = run.stage_status?.stages?.[key];
  if (entry?.status === "skipped" && typeof entry.note === "string") return "Skipped";
  if (run.status === key && run.stage_status?.blocked) {
    const code = run.stage_status.blocked.code;
    return code === "quota_exhausted" ? "Paused — quota exhausted" : "Paused — service unavailable";
  }
  return undefined;
}

const FAILURE_MESSAGES: Record<string, string> = {
  file_malformed: "This manuscript could not be checked — the file failed ingestion.",
  api_down: "An external service is unavailable. This will resume automatically.",
  quota_exhausted: "Daily AI quota reached. This will resume automatically after reset.",
};

export function CheckProgressPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: run, isLoading } = useCheckRun(id);

  if (isLoading || !run) {
    return <div className="p-4 text-xs text-ink-faint">Loading…</div>;
  }

  const steps: Step[] = STAGE_ORDER.map(({ key, label }) => ({
    label,
    state: stageState(run, key),
    note: stageNote(run, key),
  }));

  const blocked = run.stage_status?.blocked;
  const failed = run.status === "failed" ? run.stage_status?.failed : undefined;

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2.5">
        <b className="text-md text-ink">Check progress</b>
        <span className="flex-1" />
        {run.queue_position !== null && (
          <span className="rounded-pill bg-page px-2.5 py-1 text-2xs text-ink-faint">
            Queue position {run.queue_position}
          </span>
        )}
      </div>

      <Stepper steps={steps} />

      {blocked && (
        <div
          role="status"
          className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text"
        >
          {FAILURE_MESSAGES[blocked.code] ?? blocked.message}
          {blocked.resume_at && (
            <>
              {" "}
              Resumes at{" "}
              {new Date(blocked.resume_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
              .
            </>
          )}
        </div>
      )}

      {failed && (
        <div role="alert" className="rounded-control bg-status-bad-bg px-3 py-2 text-xs text-status-bad-text">
          {FAILURE_MESSAGES[failed.code] ?? failed.message}
        </div>
      )}

      {run.status === "done" && (
        <Link
          to={`/report/${run.id}`}
          className="w-fit rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary"
        >
          View readiness report
        </Link>
      )}
    </div>
  );
}
