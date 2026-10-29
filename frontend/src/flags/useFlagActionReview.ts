import { useState } from "react";
import type { FlagOut, ReportOut } from "../api/types";
import { useActionLifetime } from "../auth/useActionLifetime";
import { isAssessmentRevision } from "../report/assessmentRevision";
import { useReport } from "../report/useReport";
import { useFlag } from "./useFlag";

function sourceIdentity(flag: FlagOut) {
  return JSON.stringify([flag.id, flag.check_run_id, flag.evidence_excerpt,
    flag.citation_source_key?.kind, flag.citation_source_key?.value]);
}

export function useFlagActionReview(flag: FlagOut, action: "override" | "confirm-source" | "confirm-finding") {
  const isCurrent = useActionLifetime(`${flag.check_run_id}:${flag.id}:${action}`);
  const flagQuery = useFlag(flag.id);
  const reportQuery = useReport(flag.check_run_id);
  const [bound, setBound] = useState(() => ({ revision: flag.assessment_revision, source: sourceIdentity(flag) }));
  const [invalidated, setInvalidated] = useState(false);
  const [reviewed, setReviewed] = useState<{ flag: FlagOut; report: ReportOut }>();
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string>();
  const [sourceAffirmation, setSourceAffirmation] = useState<string>();
  const currentFlag = flagQuery.isSuccess ? flagQuery.data : undefined;
  const currentReport = reportQuery.isSuccess ? reportQuery.data : undefined;
  const eligible = (finding: FlagOut, report: ReportOut) => finding.id === flag.id
    && finding.check_run_id === flag.check_run_id && report.check_run_id === flag.check_run_id
    && !finding.overridden && report.decision === null
    && (action === "override" || action === "confirm-finding" || (finding.check_kind === "citation_integrity"
      && finding.ai_verdict_summary === "unverifiable_not_found" && Boolean(finding.citation_source_key)));
  const coherent = currentFlag && currentReport && isAssessmentRevision(currentFlag.assessment_revision)
    && currentFlag.assessment_revision === currentReport.assessment_revision;
  const changedSource = reviewed !== undefined && sourceIdentity(reviewed.flag) !== bound.source;
  const affirmationIdentity = reviewed && JSON.stringify([sourceIdentity(reviewed.flag), reviewed.flag.assessment_revision,
    currentFlag && sourceIdentity(currentFlag), currentFlag?.assessment_revision, currentReport?.assessment_revision]);
  const sourceReviewed = affirmationIdentity !== undefined && sourceAffirmation === affirmationIdentity;
  const canSubmit = !invalidated && reportQuery.fetchStatus === "idle" && flagQuery.fetchStatus === "idle"
    && coherent && isAssessmentRevision(bound.revision)
    && currentFlag.assessment_revision === bound.revision && sourceIdentity(currentFlag) === bound.source
    && eligible(currentFlag, currentReport);
  const canRebind = reviewed && coherent && reportQuery.fetchStatus === "idle" && flagQuery.fetchStatus === "idle"
    && reviewed.flag.assessment_revision === currentFlag.assessment_revision
    && sourceIdentity(reviewed.flag) === sourceIdentity(currentFlag)
    && eligible(reviewed.flag, reviewed.report) && (!changedSource || sourceReviewed);
  async function reviewCurrent() {
    if (!isCurrent() || reading) return;
    setReading(true); setInvalidated(true); setReviewed(undefined); setError(undefined); setSourceAffirmation(undefined);
    try {
      const finding = await flagQuery.refetch();
      const report = await reportQuery.refetch();
      if (!isCurrent()) return;
      if (!finding.isSuccess || !report.isSuccess || !isAssessmentRevision(finding.data?.assessment_revision)
        || finding.data.assessment_revision !== report.data?.assessment_revision) {
        setError("The current finding and assessment could not be loaded together. Your unsaved reason is kept. Retry the current review.");
        return;
      }
      setReviewed({ flag: finding.data, report: report.data });
    } catch {
      if (isCurrent()) setError("The current finding and assessment could not be loaded together. Your entered reason is kept. Retry the current review.");
    } finally { if (isCurrent()) setReading(false); }
  }
  return { canSubmit: Boolean(canSubmit), expectedRevision: bound.revision, isCurrent,
    reading, reviewed, error, changedSource, sourceReviewed,
    eligible: reviewed ? eligible(reviewed.flag, reviewed.report) : false,
    setSourceReviewed: (checked: boolean) => setSourceAffirmation(checked ? affirmationIdentity : undefined),
    reasonChanged: () => setSourceAffirmation(undefined),
    canRebind: Boolean(canRebind), reviewCurrent,
    freeze: () => { if (isCurrent()) { setInvalidated(true); setReviewed(undefined); } },
    rebind: () => {
      if (!isCurrent() || !canRebind || !reviewed) return;
      setBound({ revision: reviewed.flag.assessment_revision, source: sourceIdentity(reviewed.flag) });
      setInvalidated(false); setReviewed(undefined); setSourceAffirmation(undefined);
    },
  };
}
