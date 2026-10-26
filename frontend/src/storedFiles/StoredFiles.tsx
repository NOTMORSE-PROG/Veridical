// V-078 (BUG-410): the instructor's own Review Desk uploads and their stored
// files, kept apart from the Library (D-028). The one screen where a checked
// student's stored manuscript file can be deleted (the V-042 purge). No
// verdict, score or finding appears here; a report is only linked.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import type { StoredFileOut } from "../api/types";
import { StatusPill } from "../components/StatusPill";
import { manuscriptStatus, RUNNING_STATUSES } from "../domain/manuscriptStatus";
import { localDate as formatDate } from "../domain/localDate";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useCheckStoredFile, useDeleteStoredFile, useStoredFiles } from "./useStoredFiles";

function fileName(item: StoredFileOut): string {
  return item.original_filename ?? `Upload for ${item.group_label}`;
}

function headingId(item: StoredFileOut): string {
  return `stored-file-${item.manuscript_id}`;
}

function checkIsRunning(item: StoredFileOut): boolean {
  return item.ingest_status === "pending" || item.ingest_status === "processing" ||
    (item.latest_check_run_status !== null && RUNNING_STATUSES.has(item.latest_check_run_status));
}

function processStatus(item: StoredFileOut) {
  if (item.latest_check_run_status === "cancelled") return { label: "Check cancelled", tone: "neutral" } as const;
  if (item.latest_check_run_status === null) {
    if (item.ingest_status === "failed") return { label: "Upload preparation failed", tone: "attention" } as const;
    if (item.ingest_status !== "done") return { label: "Preparing upload", tone: "info" } as const;
  }
  return manuscriptStatus(item);
}

function StoredFileRow({ item, onDelete }: { item: StoredFileOut; onDelete: () => void }) {
  const status = processStatus(item);
  const running = checkIsRunning(item);
  const availability = useCheckStoredFile(item.manuscript_id);
  const checkingRef = useRef(false);
  function checkAvailability() {
    // Synchronous guard covers repeat clicks before the pending render, too.
    if (checkingRef.current) return;
    checkingRef.current = true;
    availability.mutate(undefined, { onSettled: () => { checkingRef.current = false; } });
  }
  const probe = availability.data;
  const recorded = item.has_stored_file && !item.purged_at;
  const initial = !probe && !availability.isError;
  let fileState = "Not checked";
  let fileDetail = "Check whether a stored copy can be found.";
  if (item.purged_at) {
    fileState = `Deleted ${formatDate(item.purged_at)}`;
    fileDetail = "";
  } else if (!item.has_stored_file || probe?.state === "not_recorded") {
    fileState = "No stored file recorded";
    fileDetail = "";
  } else if (availability.isPending) {
    fileState = "Checking storage";
    fileDetail = "Checking this file.";
  } else if (availability.isError || probe?.state === "unverified") {
    fileState = "Could not check availability";
    fileDetail = "The check could not confirm whether a stored copy exists. Try again later.";
  } else if (probe?.state === "found") {
    fileState = "Stored copy found";
    fileDetail = probe.basis === "local_metadata"
      ? "A local copy was found. Its contents were not opened."
      : "Storage confirmed a saved object. Its contents were not opened.";
  } else if (probe?.state === "missing") {
    fileState = "Stored copy not found";
    fileDetail = "No copy was found in the storage locations checked.";
  }
  const inconclusive = availability.isError || probe?.state === "unverified";
  const showTime = recorded && probe && !availability.isError && !availability.isPending;
  const checkLabel = availability.isPending ? "Checking" : initial ? "Check file availability" : "Check again";
  return (
    <li className="signal-stored-file" aria-labelledby={headingId(item)}>
      <div className="signal-stored-file__identity">
        <h2 id={headingId(item)} tabIndex={-1}>{fileName(item)}</h2>
        <p>Group {item.group_label}</p>
      </div>
      <div className="signal-stored-file__details"><dl className="signal-library-metadata">
        <div>
          <dt>Uploaded</dt>
          <dd>{formatDate(item.created_at)}</dd>
        </div>
        <div>
          <dt>Latest check</dt>
          <dd><StatusPill tone={status.tone}>{status.label}</StatusPill></dd>
        </div>
        <div>
          <dt>Stored file</dt>
          <dd>
            <StatusPill tone={recorded && (inconclusive || probe?.state === "missing") ? "attention" : "neutral"}>
              {fileState}
            </StatusPill>
            {fileDetail && <p>{fileDetail}</p>}
            {showTime && (
              <p>{inconclusive ? "Attempted" : "Checked"} {new Date(probe.checked_at).toLocaleString()}</p>
            )}
          </dd>
        </div>
      </dl></div>
      <div className="sr-only" role="status" aria-live="polite" aria-busy={availability.isPending}>
        {recorded && !availability.isIdle && `${fileName(item)}: ${fileState}.`}
      </div>
      {item.dismissed_at && (
        <p className="signal-stored-file__note">
          Removed from the Review Desk on {formatDate(item.dismissed_at)}. VERIDICAL kept the record.
        </p>
      )}
      {item.in_library && (
        <p className="signal-stored-file__note">
          Also in the Library, because it was added before the Library became a separate collection.
        </p>
      )}
      {item.has_stored_file && running && (
        <p className="signal-stored-file__note">A preparation or check is running. You can delete the stored file when it stops.</p>
      )}
      {recorded && (item.ingest_status === "pending" || item.ingest_status === "processing") && (
        <p className="signal-stored-file__note">Preparation is in progress. File status may change.</p>
      )}
      {(item.latest_check_run_id !== null || item.latest_done_check_run_id !== null || recorded) && (
        <div className="signal-stored-file__actions">
          {item.latest_done_check_run_id !== null && (
            <ActionLink to={`/report/${item.latest_done_check_run_id}/document`} variant="secondary">Open manuscript review</ActionLink>
          )}
          {item.latest_check_run_id !== null && item.latest_check_run_status !== null && RUNNING_STATUSES.has(item.latest_check_run_status) && (
            <ActionLink to={`/checks/${item.latest_check_run_id}`} variant="secondary">Open check progress</ActionLink>
          )}
          {recorded && (
            <Button type="button" variant="secondary"
              aria-busy={availability.isPending || undefined}
              aria-disabled={availability.isPending || undefined}
              aria-label={availability.isPending ? `Checking file availability: ${fileName(item)}` : initial ? `Check file availability: ${fileName(item)}` : `Check again: file availability for ${fileName(item)}`}
              onClick={checkAvailability}>{checkLabel}</Button>
          )}
          {item.has_stored_file && !running && (
            <Button type="button" variant="danger" disabled={availability.isPending} onClick={onDelete}>Delete stored file</Button>
          )}
        </div>
      )}
    </li>
  );
}

function DeleteStoredFileDialog({
  item,
  onClose,
  onDeleted,
  onRefresh,
}: {
  item: StoredFileOut;
  onClose: () => void;
  onDeleted: () => void;
  onRefresh: () => void;
}) {
  const remove = useDeleteStoredFile();
  const error = remove.error;
  const refused = error instanceof ApiError && error.status === 409;
  const uncertain = remove.isError && !refused;
  const failure = refused
    ? error.message
    : remove.isError
      ? "Refresh the file status before trying again."
      : null;
  return (
    <Dialog
      title={`Delete the stored file for “${fileName(item)}”?`}
      onClose={remove.isPending ? undefined : onClose}
      actions={(
        <>
          <Button type="button" variant="secondary" disabled={remove.isPending} onClick={onClose}>Cancel</Button>
          <Button
            type="button"
            variant="danger"
            disabled={uncertain}
            busy={remove.isPending}
            onClick={() => remove.mutate(item.manuscript_id, { onSuccess: onDeleted })}
          >
            {remove.isPending ? "Deleting…" : "Delete stored file"}
          </Button>
        </>
      )}
    >
      <p>
        This deletes the uploaded file and the text VERIDICAL extracted from it. Its pages can no longer be
        opened here. This cannot be undone.
      </p>
      {item.in_library && (
        <p>
          It is also in the Library, so it leaves the Library and later originality checks stop comparing
          against it.
        </p>
      )}
      <p>Kept: its reports and their findings, your decisions, and the audit history.</p>
      {failure && (
        <Alert tone="error" role="alert" title={refused ? "Nothing was deleted" : "Could not confirm deletion"}>
          {failure}
          {uncertain && <Button type="button" variant="secondary" onClick={onRefresh}>Refresh file status</Button>}
        </Alert>
      )}
    </Dialog>
  );
}

export function StoredFilesPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Stored files - VERIDICAL", headingRef);
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, refetch } = useStoredFiles(page);
  const [deleting, setDeleting] = useState<StoredFileOut | null>(null);
  const [deleted, setDeleted] = useState<StoredFileOut | null>(null);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  // After a delete the row's button is gone, so the dialog cannot hand focus
  // back to it; focus moves to the row itself instead (V-078 AC6).
  useEffect(() => {
    if (deleted) document.getElementById(headingId(deleted))?.focus();
  }, [deleted]);

  function changePage(next: number) {
    setDeleted(null);
    setPage(next);
    headingRef.current?.focus();
  }

  return (
    <div className="signal-route signal-page-flow signal-stored-files">
      <header className="signal-route-header">
        <div>
          <p className="signal-eyebrow">Retention</p>
          <h1 ref={headingRef} tabIndex={-1}>Stored files</h1>
          <p className="signal-route-header__intro">
            Review the manuscript files uploaded through the Review Desk, including failed uploads removed from its queues.
          </p>
          <p className="signal-route-header__intro">
            Deleting a stored file removes the upload and its extracted text; reports, decisions and the audit
            history stay. Manuscripts in the <Link to="/library">Library</Link> are managed there.
          </p>
          <p className="signal-route-header__intro">
            Use Check file availability to look for a stored copy. This checks storage, not whether the
            document opens correctly or contains the expected content.
          </p>
        </div>
      </header>

      <div className="signal-stored-files__records">
        <div className={deleted ? undefined : "sr-only"} role="status" aria-live="polite">
          {deleted && (
            <Alert tone="success" title={`The stored file for “${fileName(deleted)}” was deleted.`}>
              Its reports, your decisions and the audit history are kept.
            </Alert>
          )}
        </div>

        {isLoading && (
          <div className="signal-library-state" role="status" aria-busy="true">Loading stored files.</div>
        )}
        {isError && (
          <div className="signal-library-state" role="alert">
            <strong>Stored files could not be loaded.</strong>
            <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
          </div>
        )}
        {data && data.total === 0 && (
          <section className="signal-library-state">
            <h2>No stored files</h2>
            <p>Manuscripts you upload on the Review Desk appear here, with their stored file.</p>
            <ActionLink to="/dashboard" variant="secondary">Go to the Review Desk</ActionLink>
          </section>
        )}
        {data && data.items.length > 0 && (
          <>
            <p className="signal-stored-files__count">
              {data.total} upload{data.total === 1 ? "" : "s"}
            </p>
            <ul className="signal-stored-files__list">
              {data.items.map((item) => (
                <StoredFileRow key={`${page}-${item.manuscript_id}-${item.purged_at ?? "recorded"}`} item={item} onDelete={() => setDeleting(item)} />
              ))}
            </ul>
          </>
        )}
        {totalPages > 1 && (
          <nav className="signal-library-pagination" aria-label="Stored files pages">
            <Button type="button" variant="secondary" disabled={page === 1} onClick={() => changePage(page - 1)}>
              Previous
            </Button>
            <span>Page {page} of {totalPages}</span>
            <Button type="button" variant="secondary" disabled={page === totalPages} onClick={() => changePage(page + 1)}>
              Next
            </Button>
          </nav>
        )}
      </div>

      {deleting && (
        <DeleteStoredFileDialog
          item={deleting}
          onClose={() => setDeleting(null)}
          onRefresh={() => { setDeleting(null); void refetch(); }}
          onDeleted={() => {
            setDeleted(deleting);
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}
