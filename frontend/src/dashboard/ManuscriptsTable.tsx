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
import { useEffect, useId, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link } from "react-router";
import type { IngestFailureReason, ManuscriptListItem } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { RUNNING_STATUSES, manuscriptStatus } from "../domain/manuscriptStatus";
import { useManuscriptsPage, usePrograms } from "./useDashboard";

// V-062 (AC5): the one sentinel value that means "no program set" -- must
// match `app/groups/service.py::UNSET_PROGRAM_FILTER` exactly (a protocol
// detail between the two, not user-facing data, so it's a literal on both
// sides rather than a fetched value).
const UNSET_PROGRAM_FILTER = "__unset__";

function ProgramFilter({
  program,
  onProgramChange,
  selectRef,
}: {
  program: string | undefined;
  onProgramChange: (next: string | undefined) => void;
  selectRef: RefObject<HTMLSelectElement | null>;
}) {
  const { data: programs, isLoading } = usePrograms();
  const filterId = useId();

  if (isLoading) {
    return (
      <span role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
        Loading programs…
      </span>
    );
  }
  // No programs configured at all: a select with only "All"/"Not set"
  // has nothing real to filter between -- clutter, not a control.
  if (!Array.isArray(programs) || programs.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
      <label htmlFor={filterId} className="text-sm font-medium text-ink">
        Filter by program
      </label>
      <select
        ref={selectRef}
        id={filterId}
        value={program ?? ""}
        onChange={(event) => onProgramChange(event.target.value || undefined)}
        className="min-h-11 rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9 sm:min-h-0"
      >
        <option value="">All programs</option>
        {programs.map((p) => (
          <option key={p.id} value={p.name}>
            {p.name}
          </option>
        ))}
        <option value={UNSET_PROGRAM_FILTER}>Not set</option>
      </select>
    </div>
  );
}

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

// V-062 (`ux-critic` finding): the filter has nothing to verify its own
// results against without this -- an instructor filtering to "CS" had no
// way to confirm the rows shown actually belong to CS, or that nothing
// wrongly slipped through. Renders nothing when unset ("Not set" filter
// results correctly show no badge at all, which IS the confirmation for
// that case) -- a plain muted label, not a StatusPill, since a program
// name is reference data, not a judgment/verdict.
function ProgramBadge({ program }: { program: string | null }) {
  if (!program) return null;
  return (
    <span className="inline-flex w-fit items-center rounded-full border border-border-input bg-status-neutral-bg px-1.5 py-0.5 text-[11px] font-medium text-ink-tertiary">
      {program}
    </span>
  );
}

// V-071 (AC1): "N escalations awaiting your review" used to be a dashboard-
// wide total with nothing pointing at which row it was in --
// `newcomer`'s baseline had to open reports one at a time to find out.
// `attention` tone matches its documented use (process/workflow state,
// never a readiness verdict) -- same tone "Ingestion failed" already uses.
function EscalationBadge({ count }: { count: number }) {
  if (!count || count <= 0) return null;
  return (
    <StatusPill tone="attention">
      {count} escalation{count === 1 ? "" : "s"}
    </StatusPill>
  );
}

function RowActions({
  row,
  onRerun,
  onStartCheck,
  onSetGroup,
}: {
  row: ManuscriptListItem;
  onRerun: (manuscriptId: number) => void;
  onStartCheck: (manuscriptId: number) => void;
  onSetGroup: (manuscriptId: number) => void;
}) {
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

  // V-063 (AC6): reopens the same title-page group proposal a manuscript
  // was ingested with, any time later -- a real ingested manuscript
  // always has one to reopen, regardless of what its check runs are
  // doing, so this isn't tied to any one branch below.
  const setGroupLink =
    row.ingest_status === "done" ? (
      <button type="button" onClick={() => onSetGroup(row.id)} className={linkClass}>
        Set group
      </button>
    ) : null;

  if (row.latest_check_run_status === "done" && row.latest_check_run_id) {
    return (
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
        <Link to={`/report/${row.latest_check_run_id}`} className={linkClass}>
          Open report
        </Link>
        <button type="button" onClick={() => onRerun(row.id)} className={linkClass}>
          Re-run
        </button>
        {setGroupLink}
      </div>
    );
  }
  if (running && row.latest_check_run_id) {
    return (
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
        <Link to={`/checks/${row.latest_check_run_id}`} className={linkClass}>
          View progress
        </Link>
        {priorReportLink}
        {setGroupLink}
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
        {/* An earlier DONE run still exists for this manuscript even
            though its latest attempt failed -- a real re-run candidate,
            not a dead end (V-041). */}
        {row.latest_done_check_run_id && (
          <button type="button" onClick={() => onRerun(row.id)} className={linkClass}>
            Re-run
          </button>
        )}
        {setGroupLink}
      </div>
    );
  }
  // V-071 (BUG-055): a successfully-ingested manuscript with no run at all
  // used to have an empty Actions cell here -- the "Not checked yet" pill
  // two columns over named the exact next step and this cell offered no
  // way to take it. "New check" in the Dashboard header still works, but
  // nothing on the row itself pointed there (AC0f: an absent expected
  // control is the same defect class as a mislabelled one).
  if (row.ingest_status === "done") {
    return (
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-3">
        <button type="button" onClick={() => onStartCheck(row.id)} className={linkClass}>
          Start check
        </button>
        {setGroupLink}
      </div>
    );
  }
  // Still ingesting (ingestion failure is handled by IngestFailureButton
  // above this component, never reaches here) -- nothing to act on yet.
  // V-071 (BUG-055's second half): still a real, present grid cell, not an
  // absent one -- the desktop table's `role="row"` must always expose the
  // same cell count its own header declares four columnheaders for (ARIA
  // 1.2); returning `null` here used to make this the one row shape with
  // only 3 DOM children instead of 4.
  return <span className="sr-only">Nothing to do yet -- still processing.</span>;
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

// V-062: zero results because of an active filter is a DIFFERENT state
// from zero manuscripts existing at all -- `UploadManuscriptCta` reads as
// false ("no manuscripts yet") when manuscripts exist but just don't
// match the current program filter.
function FilteredEmptyState({ onClearFilter }: { onClearFilter: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
      <p className="text-sm text-ink-secondary">No manuscripts match this filter.</p>
      <button type="button" onClick={onClearFilter} className={linkClass}>
        Clear filter
      </button>
    </div>
  );
}

export function ManuscriptsTable({
  page,
  onPageChange,
  program,
  onProgramChange,
  onUploadManuscript,
  onRerun,
  onStartCheck,
  onSetGroup,
}: {
  page: number;
  onPageChange: (p: number) => void;
  program: string | undefined;
  onProgramChange: (next: string | undefined) => void;
  onUploadManuscript: () => void;
  onRerun: (manuscriptId: number) => void;
  onStartCheck: (manuscriptId: number) => void;
  onSetGroup: (manuscriptId: number) => void;
}) {
  const { data, isLoading, isError, refetch } = useManuscriptsPage(page, 20, program);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  useEscapeToClose(expandedId !== null, () => setExpandedId(null));
  const programSelectRef = useRef<HTMLSelectElement>(null);

  // V-062 (`ux-critic` finding, P2 a11y): "Clear filter" unmounts itself
  // the instant it's clicked, and nothing claimed focus afterward --
  // confirmed live, `document.activeElement` fell back to `<body>`.
  // Moves focus to the persistent filter select instead (same BUG-028
  // reasoning this codebase already applies elsewhere: a control that
  // disables/unmounts mid-interaction must hand focus somewhere real).
  function clearProgramFilter() {
    onProgramChange(undefined);
    programSelectRef.current?.focus();
  }

  // The filter is an independent data source from the manuscripts list --
  // it stays visible (and usable) through the list's own loading/error
  // states rather than disappearing along with the rest of the table.
  const filterControl = (
    <ProgramFilter program={program} onProgramChange={onProgramChange} selectRef={programSelectRef} />
  );

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {filterControl}
        <div role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-tertiary">
          Loading manuscripts…
        </div>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="flex flex-col gap-2">
        {filterControl}
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
      </div>
    );
  }
  if (!data) return null;

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
  const toggle = (id: number) => setExpandedId((cur) => (cur === id ? null : id));

  return (
    <div className="flex flex-col gap-2">
      {filterControl}
      {/* Mobile: card-per-row (a 4-column comparative grid isn't the right
          shape for one dominant text column + short metadata below 640px —
          WCAG 1.4.10's data-table exception is a choice, not a default). */}
      <ul className="flex flex-col gap-2 sm:hidden">
        {data.items.map((row) => {
          const { label, tone } = manuscriptStatus(row);
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
                <div className="flex flex-none flex-col items-end gap-1">
                  <StatusPill tone={tone}>{label}</StatusPill>
                  <EscalationBadge count={row.escalations_awaiting_review} />
                </div>
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
              <div className="mt-1">
                <ProgramBadge program={row.program} />
              </div>
              <div className="mt-2 flex items-center justify-between gap-2 text-xs text-ink-tertiary">
                <span>{formatDate(row.created_at)}</span>
                {isIngestFailure ? (
                  <IngestFailureButton row={row} isOpen={isOpen} onToggle={() => toggle(row.id)} />
                ) : (
                  <RowActions
                    row={row}
                    onRerun={onRerun}
                    onStartCheck={onStartCheck}
                    onSetGroup={onSetGroup}
                  />
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
            {program !== undefined ? (
              <FilteredEmptyState onClearFilter={clearProgramFilter} />
            ) : (
              <UploadManuscriptCta onUploadManuscript={onUploadManuscript} />
            )}
          </li>
        )}
      </ul>

      {/* Desktop: the grid table. */}
      <div className="hidden overflow-hidden rounded-lg border border-border sm:block">
        <div
          role="row"
          className="grid grid-cols-[minmax(0,1fr)_190px_100px_170px] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase"
        >
          <span role="columnheader">Group</span>
          <span role="columnheader">Status</span>
          <span role="columnheader">Date</span>
          <span role="columnheader">
            <span className="sr-only">Actions</span>
          </span>
        </div>
        {data.items.map((row) => {
          const { label, tone } = manuscriptStatus(row);
          const isIngestFailure = row.ingest_status === "failed";
          const isOpen = expandedId === row.id;
          const identity = manuscriptIdentity(row.group_label, row.original_filename);
          return (
            <div key={row.id}>
              <div
                role="row"
                className="grid grid-cols-[minmax(0,1fr)_190px_100px_170px] items-center gap-3 border-t border-border px-4 py-3 text-sm"
              >
                <div className="flex min-w-0 flex-col justify-center gap-0.5">
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
                  <ProgramBadge program={row.program} />
                </div>
                <div className="flex flex-col items-start gap-1">
                  <StatusPill tone={tone}>{label}</StatusPill>
                  <EscalationBadge count={row.escalations_awaiting_review} />
                </div>
                <span className="text-ink-tertiary">{formatDate(row.created_at)}</span>
                {isIngestFailure ? (
                  <IngestFailureButton row={row} isOpen={isOpen} onToggle={() => toggle(row.id)} />
                ) : (
                  <RowActions
                    row={row}
                    onRerun={onRerun}
                    onStartCheck={onStartCheck}
                    onSetGroup={onSetGroup}
                  />
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
            {program !== undefined ? (
              <FilteredEmptyState onClearFilter={clearProgramFilter} />
            ) : (
              <UploadManuscriptCta onUploadManuscript={onUploadManuscript} />
            )}
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
