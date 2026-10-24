import { useId, useState } from "react";
import type { PublicReportOut } from "../api/types";
import { sharedAssessmentNotices } from "../domain/sharedAssessmentContext";
import { Button } from "../ui/Button";

export function SharedAssessmentContext({ report }: { report: PublicReportOut }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsId = useId();
  const notices = sharedAssessmentNotices(report);
  if (!notices.length) return null;
  return <aside className="signal-assessment-context" aria-label="Assessment context">
    <span aria-hidden="true">&#9888;</span><div>
      {notices.map(notice => <p key={notice.id} id={notice.id}><strong>{notice.title}</strong>{". "}<span>{notice.text}</span></p>)}
      <p>Ask the instructor for manuscript access or clarification if needed.</p>
      {report.rubric_needs_review && Boolean(report.rubric_parse_issues?.length) && <>
        <Button variant="quiet" aria-expanded={detailsOpen} aria-controls={detailsId} onClick={() => setDetailsOpen(open => !open)}>{detailsOpen ? "Hide" : "Show"} required-format review details</Button>
        <div id={detailsId} hidden={!detailsOpen}><ul>{report.rubric_parse_issues!.map((issue, index) => <li key={index}>{issue}</li>)}</ul></div>
      </>}
    </div>
  </aside>;
}
