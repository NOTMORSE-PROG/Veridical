import { ReadinessBand } from "../ui/ReadinessBand";
import { Button } from "../ui/Button";
import { DECISION_LABEL } from "../domain/decisionTone";
import type { useFlagActionReview } from "./useFlagActionReview";

export function FlagActionCurrentReview({ review, busy, onRebind }: {
  review: ReturnType<typeof useFlagActionReview>; busy: boolean;
  onRebind: (trigger: HTMLElement) => void;
}) {
  if (review.canSubmit) return null;
  const current = review.reviewed;
  return <div className="signal-copy-flow">
    <p>This assessment changed or needs a current read. Your unsaved reason is kept. Review the current finding before confirming.</p>
    <Button variant="secondary" disabled={busy || review.reading} busy={review.reading} onClick={() => { void review.reviewCurrent(); }}>Review current assessment</Button>
    {review.error && <p role="alert">{review.error}</p>}
    {current && <div className="signal-copy-flow">
      <h3>Current finding and assessment</h3>
      <p>{current.flag.evidence_excerpt}</p>
      {current.flag.citation_source_key && <p>Source: {current.flag.citation_source_key.kind} · {current.flag.citation_source_key.value}</p>}
      <ReadinessBand status={current.report.status} />
      <p>Instructor decision: {current.report.decision ? DECISION_LABEL[current.report.decision] : "Not recorded"}.</p>
      {current.flag.overridden ? <><p>This finding is already resolved.</p><p>Recorded reason: {current.flag.override_reason}</p></>
        : current.report.decision !== null ? <p>This report has a final decision. Reopen it before changing findings.</p>
          : !review.eligible ? <p>This reference is no longer eligible for source confirmation. Review its current evidence and return to the finding to choose an available action.</p>
            : <p>This finding is open for instructor review.</p>}
      {review.changedSource && <label><input type="checkbox" checked={review.sourceReviewed} disabled={busy}
        onChange={event => review.setSourceReviewed(event.target.checked)} />I checked this current reference, and my reason describes that verification.</label>}
      {review.canRebind && <Button variant="secondary" disabled={busy} onClick={event => onRebind(event.currentTarget)}>Use my reason with this current assessment</Button>}
    </div>}
  </div>;
}
