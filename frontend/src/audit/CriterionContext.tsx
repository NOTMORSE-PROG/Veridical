// Screen 4s (F8.10): identify the requirement without rewriting the recorded judgment.
import { Link } from "react-router";
import type { AuditLogDetail } from "../api/types";
import { criterionRecordId, SAVED_CRITERION_PARAM } from "../report/savedJudgment";
import { boundCriterionContext, hasCriterionContext } from "./criterionContextBinding";
import "./criterionContext.css";

export function CriterionContext({ entry }: { entry: AuditLogDetail }) {
  if (!hasCriterionContext(entry)) return null;
  const context = boundCriterionContext(entry);
  const heading = `audit-entry-${entry.id}-criterion-context`;
  const destination = context
    ? `/report/${context.check_run_id}?${new URLSearchParams({ [SAVED_CRITERION_PARAM]: String(context.criterion_id) })}#${criterionRecordId(context.check_run_id, context.criterion_id)}`
    : "";
  return <section aria-labelledby={heading} className="signal-audit-entry-context signal-copy-flow">
    <h3 id={heading}>Criterion context</h3>
    {context ? <>
      <p>From the required-format version used for this review.</p>
      <p className="signal-audit-criterion-text">{context.criterion_text}</p>
      <dl className="signal-audit-entry-facts">
        {context.rubric_title.trim() && <div><dt>Required format</dt><dd>{context.rubric_title}</dd></div>}
        <div><dt>Version</dt><dd>{context.rubric_version}</dd></div>
      </dl>
      <p><Link className="signal-text-link" to={destination}>View this criterion in current report</Link></p>
    </> : <>
      <p>Requirement text is unavailable.</p>
      <p>The stored judgment below is unchanged.</p>
    </>}
  </section>;
}
