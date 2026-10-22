import { REPORT_READ_REQUIRED } from "./reportReadState";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";

export type ReportReadRecovery = { readFailed?: boolean; retrying?: boolean; onRetry?: () => void };

export function ReportReadNotice({ retrying, onRetry }: ReportReadRecovery) {
  return <div id="report-read-failure"><Alert title="Could not update this report" tone="warning">
    <p>Showing the last loaded report. Its readiness band, counts and decision record may have changed. {REPORT_READ_REQUIRED}</p>
    <Button variant="secondary" aria-disabled={retrying || undefined} onClick={() => { if (!retrying) onRetry?.(); }}>Retry report</Button>
    <p role="status" className="signal-field-hint">{retrying ? "Retrying the report." : ""}</p>
  </Alert></div>;
}

export function DecisionReadRecovery({ retrying, onRetry }: ReportReadRecovery) {
  return <div className="signal-attached-flow"><p>{REPORT_READ_REQUIRED}</p>
    <Button variant="secondary" aria-disabled={retrying || undefined} onClick={() => { if (!retrying) onRetry?.(); }}>Retry report</Button>
  </div>;
}
