// V-038 (F8.5, screen 4j) — the terminal gate. Placed last in the report's
// reading order deliberately: "read the evidence, then decide" is the
// only path through this screen, which is the structural answer to
// overreliance research (Bansal et al., CHI 2021, RESEARCH.md §11) -- a
// prominent decide control competing with the evidence above it is
// exactly the shape that induces blind acceptance.
import { useEffect, useRef, useState } from "react";
import type { Decision, ReportOut } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { DECISION_LABEL, DECISION_TONE } from "../domain/decisionTone";
import { DecisionModal } from "./DecisionModal";
import { ReopenModal } from "./ReopenModal";

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

const DECISION_BUTTON_CLASS =
  "flex h-11 flex-1 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45 sm:flex-none";

interface DecisionPanelProps {
  report: ReportOut;
  manuscriptLabel: string;
}

export function DecisionPanel({ report, manuscriptLabel }: DecisionPanelProps) {
  const [openDecision, setOpenDecision] = useState<Decision | null>(null);
  const [reopenOpen, setReopenOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const prevDecision = useRef(report.decision);

  // Focus moves to the section heading whenever the decision state
  // actually transitions (decide, or reopen) -- same "don't strand focus
  // on a control that no longer exists" principle useRouteFocus applies
  // to route changes, applied here to an in-page content swap.
  useEffect(() => {
    if (prevDecision.current !== report.decision) {
      headingRef.current?.focus();
    }
    prevDecision.current = report.decision;
  }, [report.decision]);

  const blocked = report.decision === null && report.pending_review_count > 0;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-panel p-4">
      <h2
        ref={headingRef}
        id="decision-heading"
        tabIndex={-1}
        className="scroll-mt-16 text-sm font-bold text-ink"
      >
        Final decision
      </h2>

      {!report.rubric_is_current && (
        <p className="rounded-md bg-status-attention-bg px-3 py-2.5 text-sm text-status-attention-text">
          A newer version of this rubric is now active. This report was generated against the
          version that was active when the check ran, not the current one. If that matters for
          your decision, you can start a new check against the current rubric version from the
          Dashboard.
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
                Decided by you on {dateFormatter.format(new Date(report.decided_at))}.
              </span>
            )}
          </div>
          {report.decision_note && (
            <p className="rounded-md border border-border bg-page px-3 py-2 text-sm text-ink-secondary break-words">
              <span className="font-medium text-ink">Note: </span>
              {report.decision_note}
            </p>
          )}
          <button
            type="button"
            onClick={() => setReopenOpen(true)}
            className="flex h-11 w-fit items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
          >
            Reopen this decision
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            Set the final outcome for {manuscriptLabel}'s defense readiness. This cannot be
            changed afterward without an explicit, reasoned reopen.
          </p>

          {blocked && (
            <p
              id="decision-blocked-note"
              className="rounded-md bg-status-attention-bg px-3 py-2.5 text-sm text-status-attention-text"
            >
              {report.pending_review_count}{" "}
              {report.pending_review_count === 1 ? "criterion" : "criteria"} still{" "}
              {report.pending_review_count === 1 ? "needs" : "need"} your review before this
              report can be decided. Resolve{" "}
              {report.pending_review_count === 1 ? "it" : "them"} above under{" "}
              <a href="#escalated-heading" className="font-medium underline">
                Needs your review
              </a>
              , then come back to decide.
            </p>
          )}

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <button
              type="button"
              disabled={blocked}
              aria-describedby={blocked ? "decision-blocked-note" : undefined}
              onClick={() => setOpenDecision("approved")}
              className={DECISION_BUTTON_CLASS}
            >
              Approve for defense
            </button>
            <button
              type="button"
              disabled={blocked}
              aria-describedby={blocked ? "decision-blocked-note" : undefined}
              onClick={() => setOpenDecision("returned")}
              className={DECISION_BUTTON_CLASS}
            >
              Return for revision
            </button>
            <button
              type="button"
              disabled={blocked}
              aria-describedby={blocked ? "decision-blocked-note" : undefined}
              onClick={() => setOpenDecision("rejected")}
              className={DECISION_BUTTON_CLASS}
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {openDecision && (
        <DecisionModal
          decision={openDecision}
          report={report}
          manuscriptLabel={manuscriptLabel}
          onClose={() => setOpenDecision(null)}
        />
      )}
      {reopenOpen && report.decision !== null && (
        <ReopenModal
          decision={report.decision}
          checkRunId={report.check_run_id}
          onClose={() => setReopenOpen(false)}
        />
      )}
    </div>
  );
}
