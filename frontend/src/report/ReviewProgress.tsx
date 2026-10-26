import type { ReportOut } from "../api/types";
import { DECISION_LABEL } from "../domain/decisionTone";

export function ReviewProgress({ report, compact = false }: { report: ReportOut; compact?: boolean }) {
  const total = report.results.length;
  const recorded = report.results.filter(row => row.outcome !== "escalated").length;
  const pending = report.pending_review_count;
  return <section className={`signal-review-progress${compact ? " signal-review-progress--compact" : ""}`} aria-label="Review progress">
    <div className="signal-review-progress__heading"><h2>Review progress</h2><span>{recorded} of {total} criteria recorded</span></div>
    {total > 0 && <progress aria-label="Criteria recorded" value={recorded} max={total} />}
    <ol>
      <li className="is-complete"><span aria-hidden="true">✓</span><div><strong>Results available</strong><p>Read the manuscript and source evidence.</p></div></li>
      <li className={pending ? "is-current" : "is-complete"} aria-current={pending ? "step" : undefined}><span aria-hidden="true">{pending ? "2" : "✓"}</span><div><strong>Review criteria</strong><p>{pending ? `${pending} ${pending === 1 ? "judgment remains" : "judgments remain"}. Each save updates this record.` : "All criterion outcomes recorded."}</p></div></li>
      <li className={report.decision ? "is-complete" : pending ? undefined : "is-current"} aria-current={!pending && !report.decision ? "step" : undefined}><span aria-hidden="true">{report.decision ? "✓" : "3"}</span><div><strong>Instructor decision</strong><p>{report.decision ? DECISION_LABEL[report.decision] : pending ? "Available after the remaining judgments." : "Ready for your final decision."}</p></div></li>
    </ol>
    {!report.decision && <a href={pending ? "#review-criteria" : "#final-decision"}>{pending ? "Continue rubric review" : "Continue to final decision"}<span aria-hidden="true"> →</span></a>}
  </section>;
}
