// BUG-553: beside the originality scope line, what the check did not compare
// and where the full limits are, so an empty lead list never reads as "no reuse".
import type { ReportOut } from "../api/types";
import { ActionLink } from "../ui/ActionLink";
import { ORIGINALITY_NOT_COMPARED, originalityCoverageNote } from "./originalityCoverage";

export function OriginalityCoverageNotice({ report }: { report: Pick<ReportOut, "check_run_id" | "integrity_check_status" | "originality_scope"> | undefined }) {
  const note = originalityCoverageNote(report);
  // A body that could not be read at all is already said by the scope line.
  if (!note || !report || note.none) return null;
  return <div className="signal-originality-coverage" role="note">
    <p>{note.sentence}</p>
    <ActionLink to={`/report/${report.check_run_id}?section=limits`} variant="secondary">{ORIGINALITY_NOT_COMPARED.limitsLink}</ActionLink>
  </div>;
}
