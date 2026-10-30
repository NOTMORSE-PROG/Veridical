// BUG-553: beside the originality scope line, what the check did not compare
// and where the full limits are, so an empty lead list never reads as "no reuse".
import { useLocation } from "react-router";
import type { ReportOut } from "../api/types";
import { ActionLink } from "../ui/ActionLink";
import { ORIGINALITY_NOT_COMPARED, originalityCoverageNote } from "./originalityCoverage";
import { focusReportSection } from "./reportSectionNavigation";

const SHOW_WAIT_FRAMES = 20;

// On the report itself the limits tab is shown a few renders after the link
// changes the address; focus its heading once it is actually visible.
function focusWhenShown(id: string, frames = SHOW_WAIT_FRAMES) {
  const target = document.getElementById(id);
  if (target && target.getClientRects().length > 0) {
    target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  } else if (frames > 0) requestAnimationFrame(() => focusWhenShown(id, frames - 1));
}

export function OriginalityCoverageNotice({ report }: { report: Pick<ReportOut, "check_run_id" | "integrity_check_status" | "originality_scope"> | undefined }) {
  const location = useLocation();
  const note = originalityCoverageNote(report);
  if (!note || !report) return null;
  return <div className="signal-originality-coverage" role="note">
    {/* "No text was compared" is already the scope line's own sentence. */}
    {!note.none && <p>{note.sentence}</p>}
    <ActionLink to={`/report/${report.check_run_id}?section=limits#coverage-statement-heading`} variant="secondary"
      onClick={(event) => {
        if (event.currentTarget.pathname === location.pathname) focusWhenShown("coverage-statement-heading");
        else focusReportSection(event, location, "coverage-statement-heading");
      }}>{ORIGINALITY_NOT_COMPARED.limitsLink}</ActionLink>
  </div>;
}
