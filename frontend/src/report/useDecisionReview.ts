import { useState } from "react";
import type { Decision, ReportOut } from "../api/types";
import { useActionLifetime } from "../auth/useActionLifetime";
import { isAssessmentRevision } from "./assessmentRevision";
import { useReport } from "./useReport";

export function useDecisionReview(report: ReportOut, action: Decision | "reopen", readFailed?: boolean) {
  const query = useReport(report.check_run_id);
  const isCurrent = useActionLifetime(`${report.check_run_id}:${action}`);
  const [revision, setRevision] = useState(report.assessment_revision);
  const [reviewed, setReviewed] = useState<ReportOut>();
  const [invalidated, setInvalidated] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string>();
  const eligible = (current: ReportOut) => current.check_run_id === report.check_run_id
    && (action === "reopen" ? current.decision !== null
      : current.decision === null && (action === "returned" || current.pending_review_count === 0));
  const current = query.isSuccess ? query.data : undefined;
  const canSubmit = !readFailed && !invalidated && query.fetchStatus === "idle" && current && isAssessmentRevision(revision)
    && current.assessment_revision === revision && report.assessment_revision === revision && eligible(current);
  const canRebind = !readFailed && reviewed && current && query.fetchStatus === "idle"
    && isAssessmentRevision(reviewed.assessment_revision)
    && reviewed.assessment_revision === current.assessment_revision && eligible(reviewed);
  async function reviewCurrent() {
    if (!isCurrent() || reading) return;
    setReading(true); setInvalidated(true); setReviewed(undefined); setError(undefined);
    try {
      const result = await query.refetch();
      if (!isCurrent()) return;
      if (!result.isSuccess || !isAssessmentRevision(result.data?.assessment_revision)) {
        setError("The current assessment could not be loaded. Your reason is kept. Retry the current review.");
        return;
      }
      setReviewed(result.data);
    } catch {
      if (isCurrent()) setError("The current assessment could not be loaded. Your reason is kept. Retry the current review.");
    } finally { if (isCurrent()) setReading(false); }
  }
  return { canSubmit: Boolean(canSubmit), canRebind: Boolean(canRebind), expectedRevision: revision,
    reviewed, reading, error, isCurrent, reviewCurrent,
    freeze: () => { if (isCurrent()) { setInvalidated(true); setReviewed(undefined); } },
    rebind: () => {
      if (!isCurrent() || !canRebind || !reviewed) return;
      setRevision(reviewed.assessment_revision); setInvalidated(false); setReviewed(undefined);
    },
  };
}
