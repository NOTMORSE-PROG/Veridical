// Read-only decision display for the public adviser view (V-040, screen
// 4l). Deliberately NOT a stripped-down DecisionPanel: DecisionPanel.tsx
// imports useDecideReport/useReopenReport and renders the Approve/
// Return/Reject/Reopen controls -- a read-only page must not carry that
// surface into its own module at all, so "this page can mutate nothing"
// stays true at the source level, not just because the buttons happen
// not to render.
import type { ReportCommon } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { DECISION_LABEL, DECISION_TONE } from "../domain/decisionTone";

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

// BUG-044: typed against ReportCommon, not ReportOut -- rendered from the
// public adviser view with a PublicReportOut, which lacks several
// instructor-only fields this component never needed anyway.
export function DecisionSummary({ report }: { report: ReportCommon }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-panel p-4">
      <h2 className="text-sm font-bold text-ink">Final decision</h2>

      {!report.rubric_is_current && (
        <p className="rounded-md bg-status-attention-bg px-3 py-2.5 text-sm text-status-attention-text">
          This report was generated against a version of the rubric that is no longer the
          currently active one.
        </p>
      )}

      {report.decision !== null ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={DECISION_TONE[report.decision]}>
              {DECISION_LABEL[report.decision]}
            </StatusPill>
            {report.decided_at && (
              <span className="text-sm text-ink-tertiary">
                Decided by the instructor on {dateFormatter.format(new Date(report.decided_at))}.
              </span>
            )}
          </div>
          {report.decision_note && (
            <p className="rounded-md border border-border bg-page px-3 py-2 text-sm text-ink-secondary break-words">
              <span className="font-medium text-ink">Note: </span>
              {report.decision_note}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-ink-secondary">
          The instructor has not made a final decision on this report yet.
        </p>
      )}
    </div>
  );
}
