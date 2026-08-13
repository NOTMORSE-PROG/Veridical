// Screen 4e manuscripts table (F8.8 first slice). Shows PIPELINE status
// (queued/running/done/failed) — the READINESS verdict (Ready/Not
// Ready/etc.) lives on the report itself (screen 4h), opened via "Open
// report", not duplicated here. V-055: colorblind-checked status pills,
// a real mobile card layout (not a scrolling grid), a working "why did
// ingestion fail" explanation (BUG-016), and a real fetch-error state.
// V-038 / ux-critic finding: a DECISION (approved/returned/rejected) is
// a third, distinct dimension from both pipeline status and readiness
// verdict -- it's the human's own terminal call, not VERIDICAL's, so
// showing it here isn't the readiness-verdict duplication this file's
// own rule above guards against. Without it an instructor triaging ~20
// manuscripts had no way to tell which they'd already decided without
// opening every report.
import { useEffect, useState } from "react";
import { Link } from "react-router";
import type { IngestFailureReason, ManuscriptListItem } from "../api/types";
import type { StatusPillTone } from "../components/StatusPill";
import { StatusPill } from "../components/StatusPill";
import { DECISION_LABEL, DECISION_TONE } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useManuscriptsPage } from "./useDashboard";

const RUNNING_STATUSES = new Set([
  "queued",
  "ingesting",
  "structural",
  "semantic",
  "integrity",
  "aggregating",
]);

const INGEST_FAILURE_COPY: Record<IngestFailureReason, string> = {
  file_too_large:
    "The uploaded file was larger than VERIDICAL currently accepts. Try a smaller file or split it into parts.",
  unreadable_format:
    "VERIDICAL could not read this file's format. Confirm it is a real PDF or DOCX file, not a renamed file of another type.",
  extraction_failed:
    "VERIDICAL could not extract readable text or structure from this file. It may be a scanned image without a text layer, or corrupted.",
};

function ingestFailureText(reason: IngestFailureReason | null): string {
  if (reason && INGEST_FAILURE_COPY[reason]) return INGEST_FAILURE_COPY[reason];
  return "This file could not be processed. The specific reason was not recorded before this feature shipped.";
}

function statusPill(row: ManuscriptListItem): { label: string; tone: StatusPillTone } {
  if (row.latest_check_run_status === "done") {
    if (row.latest_decision) {
      return { label: DECISION_LABEL[row.latest_decision], tone: DECISION_TONE[row.latest_decision] };
    }
    return { label: "Checked", tone: "success" };
  }
  if (row.latest_check_run_status === "failed") return { label: "Check failed", tone: "attention" };
  if (row.latest_check_run_status && RUNNING_STATUSES.has(row.latest_check_run_status)) {
    return { label: "Checking", tone: "info" };
  }
  if (row.ingest_status === "done") return { label: "Not checked yet", tone: "neutral" };
  if (row.ingest_status === "failed") return { label: "Ingestion failed", tone: "attention" };
  return { label: "Ingesting", tone: "info" };
}

// Escape closes whichever row's reason panel is open, from anywhere on
// the page — same dismissal model the mobile nav already uses, so the
// two disclosures in this screen behave consistently (found live: this
// one previously had no Escape handling at all).
function useEscapeToClose(isOpen: boolean, onClose: () => void) {
  useEffect(() => {
    if (!isOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);
}

function IngestFailureButton({
  row,
  isOpen,
  onToggle,
}: {
  row: ManuscriptListItem;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="inline-flex items-center gap-2">
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={`ingest-reason-${row.id}`}
        onClick={onToggle}
        className="inline-flex min-h-6 items-center text-xs font-medium text-link underline hover:text-link-hover sm:min-h-11"
      >
        Why did this fail?
      </button>
      <span
        className="text-xs text-ink-tertiary"
        title="Re-uploading a corrected file is not available on this screen yet"
      >
        Re-upload unavailable
      </span>
    </div>
  );
}

// Rendered in normal document flow (never `position: absolute`) so an
// open panel pushes the NEXT row down instead of floating over it — found
// live: two adjacent "Ingestion failed" rows (the real seed-data shape)
// meant an open panel occluded the next row's own identical button, both
// visually and in the accessibility tree (a focused-but-hidden control,
// WCAG 2.4.11).
function IngestFailurePanel({ row }: { row: ManuscriptListItem }) {
  return (
    <div
      id={`ingest-reason-${row.id}`}
      role="status"
      className="rounded-md border border-border bg-panel p-3 text-xs text-ink"
    >
      {ingestFailureText(row.ingest_failure_reason)}
    </div>
  );
}

const linkClass =
  "inline-flex min-h-6 items-center text-xs text-link underline hover:text-link-hover sm:min-h-11";

function RowActions({ row }: { row: ManuscriptListItem }) {
  const running = row.latest_check_run_status
    ? RUNNING_STATUSES.has(row.latest_check_run_status)
    : false;

  // A prior DONE run's report must stay reachable even when a newer
  // re-run supersedes it (still running, or failed) -- backend-critic
  // finding on BUG-012, V-055: the table used to follow the absolute-
  // latest run only, so a failed/in-flight re-run silently made a
  // perfectly valid earlier report unreachable from this screen.
  const priorReportLink =
    row.latest_done_check_run_id && row.latest_done_check_run_id !== row.latest_check_run_id ? (
      <Link to={`/report/${row.latest_done_check_run_id}`} className={linkClass}>
        Open prior report
      </Link>
    ) : null;

  if (row.latest_check_run_status === "done" && row.latest_check_run_id) {
    return (
      <Link to={`/report/${row.latest_check_run_id}`} className={linkClass}>
        Open report
      </Link>
    );
  }
  if (running && row.latest_check_run_id) {
    return (
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
        <Link to={`/checks/${row.latest_check_run_id}`} className={linkClass}>
          View progress
        </Link>
        {priorReportLink}
      </div>
    );
  }
  // A check-run failure (distinct from an ingestion failure) already has
  // its reason rendered on the progress screen (Progress.tsx reads
  // run.stage_status.failed) — link there rather than dead-ending, same
  // principle as BUG-016 even though this specific path predates that bug
  // ticket.
  if (row.latest_check_run_status === "failed" && row.latest_check_run_id) {
    return (
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
        <Link to={`/checks/${row.latest_check_run_id}`} className={linkClass}>
          Why did this fail?
        </Link>
        {priorReportLink}
      </div>
    );
  }
  return (
    <span
      className="text-xs text-ink-tertiary"
      title="Re-run against a new rubric version arrives later"
    >
      Re-run unavailable
    </span>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function UploadManuscriptCta({ onUploadManuscript }: { onUploadManuscript: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
      <h3 className="text-md font-bold text-ink">No manuscripts yet</h3>
      <p className="max-w-md text-sm text-ink-secondary">
        Upload a manuscript to check its readiness against your active format.
      </p>
      <button
        type="button"
        onClick={onUploadManuscript}
        className="mt-1 flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
      >
        Upload manuscript
      </button>
    </div>
  );
}

export function ManuscriptsTable({
  page,
  onPageChange,
  onUploadManuscript,
}: {
  page: number;
  onPageChange: (p: number) => void;
  onUploadManuscript: () => void;
}) {
  const { data, isLoading, isError, refetch } = useManuscriptsPage(page);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  useEscapeToClose(expandedId !== null, () => setExpandedId(null));

  if (isLoading) {
    return (
      <div role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-tertiary">
        Loading manuscripts…
      </div>
    );
  }
  if (isError) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
      >
        Could not load your manuscripts.{" "}
        <button type="button" onClick={() => refetch()} className="underline">
          Try again
        </button>
        .
      </div>
    );
  }
  if (!data) return null;

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  const toggle = (id: number) => setExpandedId((cur) => (cur === id ? null : id));

  return (
    <div className="flex flex-col gap-2">
      {/* Mobile: card-per-row (a 4-column comparative grid isn't the right
          shape for one dominant text column + short metadata below 640px —
          WCAG 1.4.10's data-table exception is a choice, not a default). */}
      <ul className="flex flex-col gap-2 sm:hidden">
        {data.items.map((row) => {
          const { label, tone } = statusPill(row);
          const isIngestFailure = row.ingest_status === "failed";
          const isOpen = expandedId === row.id;
          const identity = manuscriptIdentity(row.group_label, row.original_filename);
          return (
            <li key={row.id} className="rounded-lg border border-border bg-panel p-3">
              <div className="flex items-start justify-between gap-2">
                <span
                  className="min-w-0 flex-1 truncate text-sm font-medium text-ink"
                  title={identity.primary}
                >
                  {identity.primary}
                </span>
                <StatusPill tone={tone}>{label}</StatusPill>
              </div>
              {identity.secondary && (
                <span
                  tabIndex={0}
                  className="mt-0.5 block truncate text-xs text-ink-tertiary"
                  title={identity.secondary}
                >
                  {identity.secondary}
                </span>
              )}
              <div className="mt-2 flex items-center justify-between gap-2 text-xs text-ink-tertiary">
                <span>{formatDate(row.created_at)}</span>
                {isIngestFailure ? (
                  <IngestFailureButton row={row} isOpen={isOpen} onToggle={() => toggle(row.id)} />
                ) : (
                  <RowActions row={row} />
                )}
              </div>
              {isIngestFailure && isOpen && (
                <div className="mt-2">
                  <IngestFailurePanel row={row} />
                </div>
              )}
            </li>
          );
        })}
        {data.total === 0 && (
          <li>
            <UploadManuscriptCta onUploadManuscript={onUploadManuscript} />
          </li>
        )}
      </ul>

      {/* Desktop: the grid table. */}
      <div className="hidden overflow-hidden rounded-lg border border-border sm:block">
        <div
          role="row"
          className="grid grid-cols-[minmax(0,1fr)_140px_140px_170px] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase"
        >
          <span role="columnheader">Group</span>
          <span role="columnheader">Status</span>
          <span role="columnheader">Date</span>
          <span role="columnheader">
            <span className="sr-only">Actions</span>
          </span>
        </div>
        {data.items.map((row) => {
          const { label, tone } = statusPill(row);
          const isIngestFailure = row.ingest_status === "failed";
          const isOpen = expandedId === row.id;
          const identity = manuscriptIdentity(row.group_label, row.original_filename);
          return (
            <div key={row.id}>
              <div
                role="row"
                className="grid grid-cols-[minmax(0,1fr)_140px_140px_170px] items-center gap-3 border-t border-border px-4 py-3 text-sm"
              >
                <div className="flex min-w-0 flex-col justify-center">
                  <span className="truncate text-ink" title={identity.primary}>
                    {identity.primary}
                  </span>
                  {identity.secondary && (
                    <span
                      tabIndex={0}
                      className="truncate text-xs text-ink-tertiary"
                      title={identity.secondary}
                    >
                      {identity.secondary}
                    </span>
                  )}
                </div>
                <StatusPill tone={tone}>{label}</StatusPill>
                <span className="text-ink-tertiary">{formatDate(row.created_at)}</span>
                {isIngestFailure ? (
                  <IngestFailureButton row={row} isOpen={isOpen} onToggle={() => toggle(row.id)} />
                ) : (
                  <RowActions row={row} />
                )}
              </div>
              {isIngestFailure && isOpen && (
                <div className="border-t border-border bg-page px-4 py-3">
                  <IngestFailurePanel row={row} />
                </div>
              )}
            </div>
          );
        })}
        {data.total === 0 && (
          <div className="px-4 py-3">
            <UploadManuscriptCta onUploadManuscript={onUploadManuscript} />
          </div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Previous
          </button>
          <span className="text-ink-tertiary">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
