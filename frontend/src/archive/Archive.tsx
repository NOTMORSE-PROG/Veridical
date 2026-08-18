// Screen 4t — the F7 originality/reuse archive (V-042): which manuscripts
// still have a stored embedding/file, and an instructor-triggered purge
// per item. No auto-deletion anywhere -- FEATURES.md §10's retention
// question is still open with the adviser, so this only ever happens when
// the instructor chooses it here (charter honesty: never imply more
// happened than did).
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import type { ArchiveItemOut } from "../api/types";
import { Chip } from "../components/Chip";
import { Modal, ModalBackdrop } from "../components/Modal";
import { StatusPill, type StatusPillTone } from "../components/StatusPill";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { manuscriptStatus } from "../domain/manuscriptStatus";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useArchive, usePurgeManuscript } from "./useArchive";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

// A real 3-state combination, not 2 (`has_archive`/`purged_at` service.py
// finding): "never archived" and "purged" both read `has_archive: false`,
// so `purged_at` is what tells them apart -- collapsing this to a boolean
// pill would silently claim a never-checked manuscript once had content.
function archiveStatePill(row: ArchiveItemOut): { label: string; tone: StatusPillTone } {
  if (row.has_archive) return { label: "Archived", tone: "success" };
  if (row.purged_at) return { label: "Content removed", tone: "neutral" };
  return { label: "Not yet archived", tone: "neutral" };
}

interface PurgeTarget {
  manuscriptId: number;
  primary: string;
}

function PurgeConfirmModal({
  target,
  isPending,
  error,
  onCancel,
  onConfirm,
}: {
  target: PurgeTarget;
  isPending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <ModalBackdrop>
      <Modal
        title={`Remove ${target.primary}'s stored content?`}
        onClose={isPending ? undefined : onCancel}
        footer={
          <>
            <button
              type="button"
              onClick={onCancel}
              disabled={isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={isPending}
              className="flex h-11 items-center justify-center rounded-md bg-danger px-4 text-sm font-bold text-on-danger hover:bg-danger-hover disabled:opacity-60"
            >
              {isPending ? "Removing" : "Remove stored content"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3 text-sm" aria-busy={isPending}>
          <p className="text-ink">
            This deletes the file VERIDICAL stored for this manuscript, and the comparison data
            used to check future manuscripts against it.
          </p>
          <p className="text-ink-secondary">
            Its check history and any decision you've already made stay exactly as they are. This
            does not undo a decision or remove this manuscript from your records.
          </p>
          <p className="text-ink-secondary">
            This cannot be undone. VERIDICAL never removes content automatically. This only
            happens when you choose it here.
          </p>
          {isPending && (
            <p role="status" className="text-sm text-ink-secondary">
              Removing {target.primary}'s stored content.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm font-medium text-danger">
              <span className="sr-only">Error: </span>
              {error}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}

const gridCols = "grid-cols-[minmax(0,1fr)_120px_140px_150px_120px]";

function RowAction({
  row,
  onPurge,
  identityRef,
}: {
  row: ArchiveItemOut;
  onPurge: (target: PurgeTarget) => void;
  identityRef: (el: HTMLElement | null) => void;
}) {
  const identity = manuscriptIdentity(row.group_label, row.original_filename);
  if (row.has_archive) {
    return (
      <button
        type="button"
        onClick={() => onPurge({ manuscriptId: row.manuscript_id, primary: identity.primary })}
        aria-label={`Purge ${identity.primary}`}
        className="inline-flex min-h-6 items-center text-xs font-medium text-danger underline hover:text-danger-hover sm:min-h-11"
      >
        Purge
      </button>
    );
  }
  if (row.purged_at) {
    return (
      <span ref={identityRef} tabIndex={-1} className="text-xs text-ink-tertiary">
        Removed {formatDate(row.purged_at)}
      </span>
    );
  }
  // Never archived: nothing to purge, so no action at all -- rendering a
  // disabled control here would be the same dead-affordance anti-pattern
  // BUG-023 already established as wrong for this app.
  return null;
}

export function ArchivePage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Archive - VERIDICAL", headingRef);

  const [page, setPage] = useState(1);
  const { data, isLoading, isError, refetch } = useArchive(page);
  const purge = usePurgeManuscript();
  const [purgeTarget, setPurgeTarget] = useState<PurgeTarget | null>(null);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [justPurgedId, setJustPurgedId] = useState<number | null>(null);
  const rowRefs = useRef(new Map<number, HTMLElement>());

  // ux-critic finding (P1): calling `.focus()` synchronously right after
  // the mutation resolves ran BEFORE React committed the row's re-render,
  // so the identity span (only mounted once `has_archive` flips to
  // false) had no ref registered yet -- a silent no-op, leaving focus
  // wherever `Modal`'s own cleanup effect sent it (the just-removed
  // Purge button, itself now detached -- also a no-op). Deferring to a
  // `justPurgedId`-keyed effect guarantees this runs AFTER commit, once
  // the ref is guaranteed to exist (same "wait for the ref, don't race
  // it" fix `Manage.tsx`'s own `justChanged` effect already applies).
  useEffect(() => {
    if (justPurgedId === null) return;
    rowRefs.current.get(justPurgedId)?.focus();
    setJustPurgedId(null);
  }, [justPurgedId]);

  async function confirmPurge() {
    if (!purgeTarget) return;
    setPurgeError(null);
    const { manuscriptId, primary } = purgeTarget;
    try {
      await purge.mutateAsync(manuscriptId);
      setPurgeTarget(null);
      setLiveMessage(`${primary}'s stored content was removed.`);
      setJustPurgedId(manuscriptId);
    } catch (err) {
      setPurgeError(
        err instanceof ApiError ? err.message : "Could not remove this manuscript's content. Try again.",
      );
    }
  }

  if (isLoading) {
    return (
      <div className="p-4 sm:p-6">
        <div
          role="status"
          aria-live="polite"
          aria-busy="true"
          className="p-4 text-sm text-ink-tertiary"
        >
          Loading archive…
        </div>
      </div>
    );
  }
  if (isError) {
    return (
      <div className="p-4 sm:p-6">
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
        >
          Could not load your archive.{" "}
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

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink">
          Archive
        </h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Every manuscript VERIDICAL has processed, and whether its content is still stored for
          future originality comparisons.
        </p>
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </p>

      <Chip>{data.total} processed</Chip>

      {data.total === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
          <h3 className="text-md font-bold text-ink">Nothing here yet</h3>
          <p className="max-w-md text-sm text-ink-secondary">
            Manuscripts appear here once you upload and check one.
          </p>
          <Link to="/dashboard" className="mt-1 text-sm font-medium text-link underline hover:text-link-hover">
            Go to Dashboard
          </Link>
        </div>
      ) : (
        <>
          {/* Mobile: card-per-row, same shape ManuscriptsTable uses. */}
          <ul className="flex flex-col gap-2 lg:hidden">
            {data.items.map((row) => {
              const identity = manuscriptIdentity(row.group_label, row.original_filename);
              const check = manuscriptStatus(row);
              const state = archiveStatePill(row);
              return (
                <li key={row.manuscript_id} className="rounded-lg border border-border bg-panel p-3">
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className="min-w-0 flex-1 truncate text-sm font-medium text-ink"
                      title={identity.primary}
                    >
                      {identity.primary}
                    </span>
                    <StatusPill tone={state.tone}>{state.label}</StatusPill>
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs text-ink-tertiary">
                    <span className="flex items-center gap-2">
                      <StatusPill tone={check.tone}>{check.label}</StatusPill>
                      {formatDate(row.created_at)}
                    </span>
                    <RowAction
                      row={row}
                      onPurge={setPurgeTarget}
                      identityRef={(el) => {
                        if (el) rowRefs.current.set(row.manuscript_id, el);
                        else rowRefs.current.delete(row.manuscript_id);
                      }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>

          {/* Desktop: the grid table. */}
          <div className="hidden overflow-hidden rounded-lg border border-border lg:block">
            <div
              role="row"
              className={`grid ${gridCols} gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase`}
            >
              <span role="columnheader">Manuscript</span>
              <span role="columnheader">Ingested</span>
              <span role="columnheader">Check status</span>
              <span role="columnheader">Archive</span>
              <span role="columnheader">
                <span className="sr-only">Actions</span>
              </span>
            </div>
            {data.items.map((row) => {
              const identity = manuscriptIdentity(row.group_label, row.original_filename);
              const check = manuscriptStatus(row);
              const state = archiveStatePill(row);
              return (
                <div
                  key={row.manuscript_id}
                  role="row"
                  className={`grid ${gridCols} items-center gap-3 border-t border-border px-4 py-3 text-sm`}
                >
                  <span className="truncate text-ink" title={identity.primary}>
                    {identity.primary}
                  </span>
                  <span className="text-ink-tertiary">{formatDate(row.created_at)}</span>
                  <StatusPill tone={check.tone}>{check.label}</StatusPill>
                  <StatusPill tone={state.tone}>{state.label}</StatusPill>
                  <RowAction
                    row={row}
                    onPurge={setPurgeTarget}
                    identityRef={(el) => {
                      if (el) rowRefs.current.set(row.manuscript_id, el);
                      else rowRefs.current.delete(row.manuscript_id);
                    }}
                  />
                </div>
              );
            })}
          </div>
        </>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
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
            onClick={() => setPage((p) => p + 1)}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Next
          </button>
        </div>
      )}

      <p className="text-sm text-ink-tertiary">
        VERIDICAL never removes stored content automatically. Purging is something you choose to
        do here, one manuscript at a time.
      </p>

      {purgeTarget && (
        <PurgeConfirmModal
          target={purgeTarget}
          isPending={purge.isPending}
          error={purgeError}
          onCancel={() => {
            setPurgeTarget(null);
            setPurgeError(null);
          }}
          onConfirm={confirmPurge}
        />
      )}
    </div>
  );
}
