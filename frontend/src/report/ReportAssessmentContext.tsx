import type { AssessmentNotice } from "../domain/reportAssessmentContext";

export function ReportAssessmentContext({ notices }: { notices: AssessmentNotice[] }) {
  if (notices.length === 0) return null;
  return <aside className="signal-assessment-context" aria-label="Assessment context">
    <span aria-hidden="true">&#9888;</span><div>{notices.map(notice => <p key={notice.id} id={notice.id}><strong>{notice.title}</strong>{". "}<span>{notice.text}</span></p>)}</div>
  </aside>;
}


export function ActionAssessmentContext({ notices, id, about }: { notices: AssessmentNotice[]; id: string; about?: string }) {
  if (notices.length === 0) return null;
  return <p id={id} className="signal-field-hint signal-action-assessment-context">{about && <>{about} </>}<strong>{notices.map(notice => notice.actionText).join(" ")}</strong></p>;
}
