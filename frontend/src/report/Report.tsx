// Screen 4h — Readiness report (F8.1-F8.2, minimal/read-only in V2:
// annotation/override arrive V-026, the decision flow V-038). Escalated
// and not_applicable/unverifiable rows are visually distinct from a real
// pass/fail (taxonomy honesty, charter rule 9) — never dressed up as a
// finding.
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { Decision } from "../api/types";
import { Chip } from "../components/Chip";
import { RubricNeedsReviewBanner } from "../components/RubricNeedsReviewBanner";
import { StatusPill } from "../components/StatusPill";
import { TestModeBanner } from "../components/TestModeBanner";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { READINESS_LABEL, READINESS_TONE } from "../domain/readinessTone";
import { useRouteFocus } from "../routing/useRouteFocus";
import { DECISION_LABEL } from "../domain/decisionTone";
import { DecisionPanel } from "./DecisionPanel";
import { EscalatedPanel } from "./EscalatedPanel";
import { FlagsPanel } from "./FlagsPanel";
import {
  NEEDS_REVIEW_OUTCOMES,
  ResultsTable,
  excludedFromScoreCount,
  explainer,
  pendingDisclosure,
} from "./ResultsTable";
import { ShareModal } from "./ShareModal";
import { useExportReportPdf, useReport } from "./useReport";
import { useShareLink } from "./useShare";

function decisionLinkLabel(decision: Decision): string {
  return `Decision: ${DECISION_LABEL[decision]}`;
}

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

export function ReportPage() {
  const { checkRunId } = useParams<{ checkRunId: string }>();
  const id = Number(checkRunId);
  const { data: report, isPending, isError, error, refetch } = useReport(id);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);
  const exportPdf = useExportReportPdf(id);
  const exportErrorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (exportPdf.isError) exportErrorRef.current?.focus();
  }, [exportPdf.isError]);
  // ux-critic finding (P1, live-reproduced): `exportPdf.isPending` is a
  // value from the LAST render, not updated in place -- N clicks landing
  // in the same synchronous tick (before React re-renders) all read the
  // same stale "not pending" value, so checking it in the handler alone
  // doesn't actually stop a rapid double-click (verified: a naive `if
  // (exportPdf.isPending) return` guard still let 3 rapid clicks through).
  // A plain mutable ref, set synchronously and cleared in onSettled
  // (fires on both success AND error), is the only guard that's actually
  // synchronous with the click itself.
  const exportInFlightRef = useRef(false);
  const [shareOpen, setShareOpen] = useState(false);
  // Fetched eagerly (cheap: one DB-only read, zero AI-quota cost), not
  // lazily on click -- Nielsen's visibility-of-system-status: a report
  // that's already been externally shared is meaningfully different
  // system state, and the semi-confidential framing in the modal only
  // works if the instructor is reminded every time they view the
  // report, not just when they think to click Share.
  const { data: shareLink } = useShareLink(id);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-col gap-2 border-b border-border pb-3">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-ink-tertiary">
          <Link to="/dashboard" className="text-link underline hover:text-link-hover">
            Dashboard
          </Link>
          <span aria-hidden="true">/</span>
        </nav>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
              Readiness report
            </h1>
            {report &&
              (() => {
                const identity = manuscriptIdentity(
                  report.manuscript_group_label,
                  report.manuscript_original_filename,
                );
                return (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Chip maxWidthClass="max-w-[240px]" title={identity.primary}>
                      {identity.primary}
                    </Chip>
                    <Chip maxWidthClass="max-w-[240px]" title={report.rubric_title}>
                      {report.rubric_title}
                    </Chip>
                  </div>
                );
              })()}
          </div>
          {report && (
            <div className="flex flex-wrap items-center gap-3">
              <StatusPill tone={READINESS_TONE[report.status]}>{READINESS_LABEL[report.status]}</StatusPill>
              {report.composite_score !== null && (
                <span className="text-xl font-bold text-ink sm:text-2xl">{report.composite_score}%</span>
              )}
              <Link
                to={`/report/${report.check_run_id}/document`}
                className="text-sm text-link underline hover:text-link-hover"
              >
                View manuscript
              </Link>
              <Link
                to={`/audit?check_run_id=${report.check_run_id}`}
                className="text-sm text-link underline hover:text-link-hover"
              >
                View audit trail
              </Link>
              <a href="#decision-heading" className="text-sm text-link underline hover:text-link-hover">
                {report.decision === null ? "Go to final decision" : decisionLinkLabel(report.decision)}
              </a>
              <button
                type="button"
                onClick={() => {
                  if (exportInFlightRef.current) return;
                  exportInFlightRef.current = true;
                  exportPdf.mutate(undefined, {
                    onSettled: () => {
                      exportInFlightRef.current = false;
                    },
                  });
                }}
                disabled={exportPdf.isPending}
                aria-busy={exportPdf.isPending ? "true" : undefined}
                aria-describedby="export-pdf-note"
                className="inline-flex items-center gap-1.5 text-sm font-medium text-link underline hover:text-link-hover disabled:no-underline disabled:opacity-60"
              >
                {exportPdf.isPending && <SpinnerIcon />}
                {exportPdf.isPending ? "Exporting PDF." : "Export PDF"}
              </button>
              <button
                type="button"
                onClick={() => setShareOpen(true)}
                className="text-sm font-medium text-link underline hover:text-link-hover"
              >
                {shareLink ? "Share (link active)" : "Share"}
              </button>
            </div>
          )}
        </div>
        {report && (
          // ux-critic finding: the exported PDF's own accessibility
          // limitation (reportlab produces an untagged PDF -- no
          // semantic reading order for assistive tech) was disclosed
          // only in a code comment nobody using the product would read.
          // Stated plainly here instead (charter judgment #4: disclose
          // limitations before being asked).
          <p id="export-pdf-note" className="text-xs text-ink-tertiary">
            Downloaded PDFs are not optimized for screen readers. Use this on-screen report for
            accessible review.
          </p>
        )}
        {exportPdf.isPending && (
          <p role="status" aria-live="polite" className="sr-only">
            Exporting this report to PDF.
          </p>
        )}
        {exportPdf.isError && (
          <p
            ref={exportErrorRef}
            role="alert"
            tabIndex={-1}
            className="text-sm font-medium text-danger"
          >
            <span className="sr-only">Error: </span>
            {exportPdf.error instanceof ApiError
              ? exportPdf.error.message
              : "Could not export this report. Try again."}
          </p>
        )}
      </header>

      {/* V-041: historical orientation about a DIFFERENT (earlier) run,
          kept visually separate from the explainer box below (this
          run's own authoritative "why this verdict" reasoning) so the
          two are never read as one blended justification -- same
          category-separation discipline as the KpiCards/Report tone
          fix (V-056). Only renders when a real prior run exists; never
          invents rubric version numbers ReportOut doesn't carry. */}
      {report && report.previous_status && (
        <p className="flex flex-wrap items-center gap-1.5 text-sm text-ink-secondary">
          <span>Previously</span>
          <StatusPill tone={READINESS_TONE[report.previous_status]}>
            {READINESS_LABEL[report.previous_status]}
          </StatusPill>
          {report.previous_composite_score !== null && <span>({report.previous_composite_score}%)</span>}
          <span aria-hidden="true">&rarr;</span>
          <span>now</span>
          <StatusPill tone={READINESS_TONE[report.status]}>{READINESS_LABEL[report.status]}</StatusPill>
          {report.composite_score !== null && <span>({report.composite_score}%)</span>}
          <span>.</span>
        </p>
      )}

      {report && <TestModeBanner llmMode={report.llm_mode} />}
      {report && (
        <RubricNeedsReviewBanner
          needsReview={report.rubric_needs_review}
          issues={report.rubric_parse_issues}
        />
      )}

      {isPending ? (
        <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-6 text-sm text-ink-secondary">
          <SpinnerIcon />
          Loading report.
        </div>
      ) : isError || !report ? (
        <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
          {error instanceof ApiError && error.code === "conflict" ? (
            <>
              This check hasn't finished yet. Its report isn't ready.{" "}
              <Link to={`/checks/${id}`} className="font-medium underline">
                View check progress
              </Link>
              .
            </>
          ) : (
            <>
              {error instanceof ApiError ? error.message : "This report couldn't be loaded."}{" "}
              <button type="button" onClick={() => refetch()} className="font-medium underline">
                Try again
              </button>
              .
            </>
          )}
        </div>
      ) : (
        <>
          <p className="rounded-lg bg-status-info-bg px-4 py-2.5 text-sm text-status-info-text">
            {explainer(report, "#flags-heading")}
            {pendingDisclosure(report.pending_review_count, excludedFromScoreCount(report.results))}
          </p>

          <EscalatedPanel checkRunId={report.check_run_id} />

          <FlagsPanel checkRunId={report.check_run_id} />

          {/* Deliberately flatter than the escalated panel above (V-056):
              this content is already decided, reference-only — a
              lighter border keeps it visually receding rather than
              competing with the panel that still needs the instructor's
              input. */}
          <ResultsTable
            results={report.results.filter((r) => !NEEDS_REVIEW_OUTCOMES.has(r.outcome))}
            totalCount={report.results.length}
          />

          <DecisionPanel
            report={report}
            manuscriptLabel={
              manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename)
                .primary
            }
          />
        </>
      )}
      {shareOpen && report && (
        <ShareModal
          checkRunId={report.check_run_id}
          manuscriptLabel={
            manuscriptIdentity(report.manuscript_group_label, report.manuscript_original_filename)
              .primary
          }
          onClose={() => setShareOpen(false)}
        />
      )}
    </div>
  );
}
