// V-073 screen 4b/4e — Signal Review Desk. The page is a prioritized work
// queue, not an analytics dashboard: each record names what happened, what
// still needs the instructor, and one honest next action.
import { type FormEvent, type MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import type { ManuscriptListItem } from "../api/types";
import { GroupProposalDialog } from "../check/GroupProposalDialog";
import { NewCheckModal } from "../check/NewCheck";
import { UploadManuscriptModal } from "../check/UploadManuscriptModal";
import { REVIEW_DESK_PAGE_SIZE, UNSET_PROGRAM_FILTER } from "../config/ui";
import {
  type ReviewDeskQuery,
  type ReviewDeskQueue,
  type ReviewDeskSort,
  reviewDeskPath,
  useDismissFailedManuscript,
  useReviewDeskPage,
} from "../dashboard/useReviewDesk";
import { useDashboardStats, useManuscriptsPage, usePrograms } from "../dashboard/useDashboard";
import { DECISION_LABEL } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { WorkflowLink } from "../routing/WorkflowLink";
import { RerunModal } from "../rubric/RerunModal";
import { FormatReviewStateTag } from "../rubric/RubricReviewParts";
import { UploadRubricModal } from "../rubric/UploadRubricModal";
import { useRubricFamilies } from "../rubric/useRubric";
import { ActionLink, TrailingChevronIcon } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ProcessStatus } from "../ui/ProcessStatus";
import { ReadinessBand } from "../ui/ReadinessBand";
import { isPagePastEnd } from "../api/fieldRefusal";
import { PAGE_PAST_END_COPY } from "../domain/pagePastEnd";

const QUEUES: ReadonlyArray<{
  id: ReviewDeskQueue;
  label: string;
  description: string;
}> = [
  { id: "needs_review", label: "Needs you", description: "Unresolved or recovery work" },
  { id: "not_checked", label: "Not checked", description: "Uploaded manuscripts awaiting a check" },
  { id: "checking", label: "In progress", description: "Checks currently running" },
  {
    id: "ready_to_decide",
    label: "Ready to decide",
    description: "Checks finished. Review assessment limits before recording your decision.",
  },
  { id: "complete", label: "Complete", description: "Latest check finished. Instructor decision recorded." },
] as const;

const FAILURE_COPY = {
  file_too_large: "The file was larger than VERIDICAL currently accepts.",
  unreadable_format: "VERIDICAL could not read this file as a supported PDF or DOCX.",
  extraction_failed: "VERIDICAL could not prepare readable text from this file.",
} as const;

function isQueue(value: string | null): value is ReviewDeskQueue {
  return value === "needs_review"
    || value === "checking"
    || value === "ready_to_decide"
    || value === "complete"
    || value === "not_checked";
}

function isSort(value: string | null): value is ReviewDeskSort {
  return value === "newest"
    || value === "oldest"
    || value === "group_asc"
    || value === "needs_review_desc";
}

function queueFromParams(params: URLSearchParams): ReviewDeskQueue {
  const requested = params.get("queue");
  if (isQueue(requested)) return requested;
  if (params.get("needs_review") === "true") return "needs_review";
  if (params.get("status") === "checking") return "checking";
  if (params.get("status") === "checked") return "ready_to_decide";
  if (params.get("status") === "decided") return "complete";
  if (params.get("status") === "not_checked") return "not_checked";
  return "needs_review";
}

function formatActivity(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(iso));
}

function recordProcess(row: ManuscriptListItem) {
  // BUG-190: a check run can only reach a terminal or active pipeline state after the
  // manuscript became usable. Some legacy records still carry an older ingest
  // value, so the newer check-run evidence must win; otherwise one card can
  // claim both "Preparing manuscript" and "Record instructor decision."
  if (row.latest_check_run_status) return row.latest_check_run_status;
  if (row.ingest_status === "failed") return "upload_failed" as const;
  if (row.purged_at) return "source_deleted" as const;
  if (row.ingest_status === "pending" || row.ingest_status === "processing") {
    return "preparing" as const;
  }
  return "not_checked" as const;
}

function QueueCount({
  queue,
  stats,
  activeQueue,
  activeTotal,
  statsError,
  matching,
}: {
  queue: ReviewDeskQueue;
  stats?: ReturnType<typeof useDashboardStats>["data"];
  activeQueue: ReviewDeskQueue;
  activeTotal?: number;
  statsError: boolean;
  matching: boolean;
}) {
  if (queue === "complete") {
    const selected = queue === activeQueue;
    const count = selected ? activeTotal : statsError ? undefined : stats?.complete_queue_count;
    return typeof count === "number"
      ? <span>{count}{selected && matching ? " matching" : ""} manuscript{count === 1 ? "" : "s"}</span>
      : null;
  }
  if (queue === "not_checked") {
    const count = stats?.not_checked_count;
    return count === undefined ? null : <span>{count} manuscript{count === 1 ? "" : "s"}</span>;
  }
  if (queue === activeQueue && activeTotal !== undefined) {
    return <span>{activeTotal} manuscript{activeTotal === 1 ? "" : "s"}</span>;
  }
  if (!stats) return null;
  let count: number | null = null;
  // BUG-211: `ready_to_decide_count` is the real predicate (latest run
  // done, zero unresolved escalations, no decision yet) computed
  // server-side -- it used to be guessed here as `manuscripts_checked -
  // needs_review_count - decided_count`, which silently diverges because
  // a manuscript's readiness BAND (needs_review_count) and whether it
  // currently has unresolved escalations are different facts.
  if (queue === "ready_to_decide") count = stats.ready_to_decide_count;
  // BUG-212: `needs_review`/`checking` used to fall through to `null`
  // here whenever they weren't the active tab -- no equivalent stat
  // field existed for either predicate at all.
  if (queue === "needs_review") count = stats.needs_attention_count;
  if (queue === "checking") count = stats.checking_count;
  if (count === null) return null;
  return <span>{count} manuscript{count === 1 ? "" : "s"}</span>;
}

function FirstUse({ onUpload }: { onUpload: () => void }) {
  return (
    <section className="signal-first-use" aria-labelledby="first-use-heading">
      <div className="signal-first-use__mark" aria-hidden="true"><span /></div>
      <p className="signal-eyebrow">Prepare · Step 1</p>
      <h2 id="first-use-heading">No required format yet</h2>
      <p>
        Start with the required format document your program uses. VERIDICAL prepares
        criteria for your review; nothing checks a manuscript until you confirm them.
      </p>
      <Button variant="brand" data-tour="upload-format-cta" onClick={onUpload}>
        Upload required format
      </Button>
      <ol aria-label="First check setup">
        <li><strong>1</strong><span>Upload the required format</span></li>
        <li><strong>2</strong><span>Review the prepared criteria</span></li>
        <li><strong>3</strong><span>Add and check a manuscript</span></li>
      </ol>
    </section>
  );
}

function RecordPrimaryAction({
  row,
  onStartCheck,
  onUploadReplacement,
}: {
  row: ManuscriptListItem;
  onStartCheck: (id: number) => void;
  onUploadReplacement: () => void;
}) {
  if (row.purged_at) {
    if (row.latest_done_check_run_id) return <ActionLink id={`review-desk-primary-${row.id}`} to={`/report/${row.latest_done_check_run_id}/document`}>Open saved review</ActionLink>;
    if (row.latest_check_run_id) return <ActionLink id={`review-desk-primary-${row.id}`} to={`/checks/${row.latest_check_run_id}`}>View check history</ActionLink>;
    return <Button variant="secondary" onClick={onUploadReplacement}>Upload manuscript again</Button>;
  }
  if (row.ingest_status === "failed") {
    return <Button variant="secondary" onClick={onUploadReplacement}>Try a corrected file</Button>;
  }
  if (row.latest_check_run_status === "done" && row.latest_check_run_id) {
    const label = row.latest_decision
      ? "Open recorded review"
      : row.escalations_awaiting_review > 0
      ? `Review ${row.escalations_awaiting_review} unresolved ${row.escalations_awaiting_review === 1 ? "criterion" : "criteria"}`
      : "Review manuscript and decide";
    const description = row.latest_decision
      ? `${label}. Opens the manuscript and recorded analysis. Reopen the decision before making changes.`
      : `${label}. Opens the manuscript and analysis.`;
    const destination = `/report/${row.latest_check_run_id}/document${!row.latest_decision && row.escalations_awaiting_review > 0 ? "?view=decision&pane=review" : ""}`;
    return <ActionLink id={`review-desk-primary-${row.id}`} to={destination} aria-label={description}>{label}</ActionLink>;
  }
  if (row.latest_check_run_status && row.latest_check_run_id) {
    if (row.latest_check_run_status === "cancelled") {
      return <Button onClick={() => onStartCheck(row.id)}>Start a fresh check</Button>;
    }
    return (
      <ActionLink id={`review-desk-primary-${row.id}`} to={`/checks/${row.latest_check_run_id}`}>
        {row.latest_check_run_status === "failed" ? "Review check failure" : "View current check"}
      </ActionLink>
    );
  }
  if (row.ingest_status === "done") {
    return <Button onClick={() => onStartCheck(row.id)}>Start check</Button>;
  }
  return <span className="signal-record__waiting">No action yet. The file is still being prepared.</span>;
}

function WorkQueueRecord({
  row,
  onStartCheck,
  onRerun,
  onSetGroup,
  onUploadReplacement,
}: {
  row: ManuscriptListItem;
  onStartCheck: (id: number) => void;
  onRerun: (id: number) => void;
  onSetGroup: (id: number) => void;
  onUploadReplacement: () => void;
}) {
  const dismiss = useDismissFailedManuscript();
  const identity = manuscriptIdentity(row.group_label, row.original_filename);
  // BUG-066: prefer the specific reason the failing check itself computed
  // (real MB/page numbers, e.g. "larger than the 40 MB upload limit") over
  // the generic per-bucket sentence -- three different underlying caps
  // (raw upload size, PDF page count, DOCX decompressed size) all set the
  // same `file_too_large` reason, so a single blanket number would be
  // wrong for two of the three. Falls back honestly, never guesses.
  const failureCopy = row.ingest_failure_detail
    ?? (row.ingest_failure_reason
      ? FAILURE_COPY[row.ingest_failure_reason]
      : "This file could not be prepared. A specific reason was not recorded.");

  return (
    <li className="signal-record">
      <div className="signal-record__identity">
        <h3>{identity.primary}</h3>
        {identity.secondary && <p title={identity.secondary}>{identity.secondary}</p>}
        <dl>
          <div><dt>Program</dt><dd>{row.program ?? "Not set"}</dd></div>
          <div><dt>Uploaded</dt><dd>{formatActivity(row.created_at)}</dd></div>
        </dl>
      </div>
      <div className="signal-record__state">
        <span className="signal-record__label">Process</span>
        <ProcessStatus status={recordProcess(row)} />
        {row.ingest_status === "failed" && <p>{failureCopy}</p>}
        {row.purged_at && <p>Stored file deleted. Upload this manuscript again to start a new check. Existing reports, decisions and audit history are kept.</p>}
      </div>
      <div className="signal-record__assessment">
        <span className="signal-record__label">System readiness</span>
        {row.latest_readiness
          ? <ReadinessBand status={row.latest_readiness} />
          : <span className="signal-record__not-available">Not available yet</span>}
        {row.escalations_awaiting_review > 0 && (
          <p>
            <strong>{row.escalations_awaiting_review}</strong>{" "}
            {row.latest_decision
              ? `${row.escalations_awaiting_review === 1 ? "criterion was" : "criteria were"} unresolved when this decision was recorded.`
              : `${row.escalations_awaiting_review === 1 ? "criterion task needs" : "criterion tasks need"} you`}
          </p>
        )}
        {row.latest_decision && (
          <p>Instructor decision: <strong>{DECISION_LABEL[row.latest_decision]}</strong></p>
        )}
      </div>
      <div className="signal-record__actions">
        <RecordPrimaryAction
          row={row}
          onStartCheck={onStartCheck}
          onUploadReplacement={onUploadReplacement}
        />
        {row.purged_at && row.latest_check_run_id && <Button variant="secondary" onClick={onUploadReplacement}>Upload manuscript again</Button>}
        <details>
          <summary>More actions</summary>
          <div>
            {row.ingest_status === "done" && (
              <button type="button" onClick={() => onSetGroup(row.id)}>Review group details</button>
            )}
            {row.latest_done_check_run_id && row.latest_done_check_run_id !== row.latest_check_run_id && (
              // BUG-108: this menu mixes real navigation (this Link) with
              // in-place actions (the plain <button>s around it), sharing
              // one CSS rule with nothing to tell them apart before a
              // click -- same trailing-chevron marker ActionLink carries.
              <WorkflowLink id={`review-desk-prior-${row.id}`} to={`/report/${row.latest_done_check_run_id}`}>
                Open prior report
                <TrailingChevronIcon />
              </WorkflowLink>
            )}
            {!row.purged_at && (row.latest_done_check_run_id ? (
              <button type="button" onClick={() => onRerun(row.id)}>Run again</button>
            ) : (
              row.latest_check_run_status === "failed" && (
                // BUG-137: a manuscript whose very first-ever check run
                // failed has no done run for `onRerun`'s RerunModal to key
                // off (that modal is scoped to re-running against a newly
                // -activated rubric VERSION, V-041 -- it can't help here).
                // `RecordPrimaryAction` above already links to "Review
                // check failure," which explains why it failed but offers
                // no way to actually try again -- this reuses the same
                // plain new-check flow "Start check" uses elsewhere.
                <button type="button" onClick={() => onStartCheck(row.id)}>Start a fresh check</button>
              )
            ))}
            {row.ingest_status === "failed" && (
              <button
                type="button"
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(row.id)}
              >
                {dismiss.isPending ? "Removing…" : "Remove from Review Desk"}
              </button>
            )}
          </div>
        </details>
        {row.ingest_status === "failed" && (
          <p className="signal-record__retention">
            VERIDICAL keeps the failed record. Review its retention status in{" "}
            <Link to="/stored-files">Stored files</Link>.
          </p>
        )}
        {dismiss.isError && (
          <Alert tone="error" role="alert" title="Could not move this record">
            Try again. Nothing was deleted.
          </Alert>
        )}
      </div>
    </li>
  );
}

function ReviewDesk({
  onUploadManuscript,
  onStartCheck,
  onRerun,
  onSetGroup,
  noManuscripts = false,
}: {
  onUploadManuscript: () => void;
  onStartCheck: (id: number) => void;
  onRerun: (id: number) => void;
  onSetGroup: (id: number) => void;
  noManuscripts?: boolean;
}) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const queue = queueFromParams(params);
  const requestedSort = params.get("sort");
  const sort = isSort(requestedSort)
    ? requestedSort
    : queue === "needs_review" ? "needs_review_desc" : "newest";
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
  const q = params.get("q")?.trim() || undefined;
  const program = params.get("program") || undefined;
  const [searchDraft, setSearchDraft] = useState(q ?? "");
  useEffect(() => setSearchDraft(q ?? ""), [q]);
  const query: ReviewDeskQuery = { queue, q, program, sort, page, pageSize: REVIEW_DESK_PAGE_SIZE };
  const { data, isPending, isError, error, fetchStatus, refetch } = useReviewDeskPage(query);
  const pastEnd = isError && isPagePastEnd(error);
  const queueHeading = useRef<HTMLHeadingElement>(null);
  const [queueAction, setQueueAction] = useState<{
    path: string;
    originLocationKey: string;
    adoptedLocationKey?: string;
    kind: "page" | "retry";
  }>();
  const currentPath = reviewDeskPath(query);
  useEffect(() => {
    if (!queueAction) return;
    // A local update can render before the router commits its transition.
    // Keep the target marker through that origin render and offline pauses.
    const abandoned = queueAction.adoptedLocationKey !== undefined
      ? location.key !== queueAction.adoptedLocationKey
      : location.key !== queueAction.originLocationKey
        && (queueAction.kind === "retry" || queueAction.path !== currentPath);
    const settledPage = queueAction.kind === "page"
      && queueAction.path === currentPath && fetchStatus === "idle";
    if (abandoned || settledPage) {
      setQueueAction((current) => current === queueAction ? undefined : current);
    } else if (queueAction.kind === "page" && queueAction.path === currentPath
      && queueAction.adoptedLocationKey === undefined) {
      setQueueAction((current) => current === queueAction
        ? { ...current, adoptedLocationKey: location.key } : current);
    }
  }, [queueAction, currentPath, location.key, fetchStatus]);
  const { data: stats, isError: statsError } = useDashboardStats();
  const { data: programs } = usePrograms();

  function update(next: Record<string, string | undefined>, replace = false, paging = false) {
    if (!paging) setQueueAction(undefined);
    const updated = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) updated.set(key, value);
      else updated.delete(key);
    }
    setParams(updated, { replace });
  }

  function beginQueueAction(event: MouseEvent<HTMLButtonElement>, next: ReviewDeskQuery, kind: "page" | "retry") {
    const control = event.currentTarget;
    if (control.isConnected && !control.disabled && document.activeElement === control && document.hasFocus()) {
      queueHeading.current?.focus();
    }
    const action = { path: reviewDeskPath(next), originLocationKey: location.key, kind };
    setQueueAction(action);
    return action;
  }

  function changePage(event: MouseEvent<HTMLButtonElement>, nextPage: number) {
    beginQueueAction(event, { ...query, page: nextPage }, "page");
    update({ page: String(nextPage) }, false, true);
  }

  function retryQueue(event: MouseEvent<HTMLButtonElement>) {
    const action = beginQueueAction(event, query, "retry");
    void refetch().finally(() => {
      setQueueAction((current) => current === action ? undefined : current);
    });
  }

  function selectQueue(next: ReviewDeskQueue) {
    const status = next === "needs_review" ? "needs_attention"
      : next === "checking" ? "checking"
      : next === "ready_to_decide" ? "checked"
        : next === "complete" ? "decided"
          : next === "not_checked" ? "not_checked"
            : undefined;
    update({
      queue: next,
      status,
      needs_review: next === "ready_to_decide" ? "false" : undefined,
      page: undefined,
    });
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    update({ q: searchDraft.trim() || undefined, page: undefined });
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const queueTitle = QUEUES.find((item) => item.id === queue)?.label ?? "Needs you";
  const matching = Boolean(q?.trim() || program?.trim());
  const ownedRequest = queueAction?.path === currentPath
    && (queueAction.adoptedLocationKey === undefined || queueAction.adoptedLocationKey === location.key)
    && (queueAction.kind === "page" || queueAction.originLocationKey === location.key);
  const loading = isPending || (ownedRequest && fetchStatus !== "idle");
  const usableList = data !== undefined && !loading && !isError;
  const loadingMessage = `Loading page ${page} in ${queueTitle}.`;

  return (
    <>
      <nav aria-label="Review Desk queues" className="signal-queue-tabs">
        {QUEUES.map((item) => (
          <button
            type="button"
            key={item.id}
            aria-pressed={queue === item.id}
            onClick={() => selectQueue(item.id)}
          >
            <span>{item.label}</span>
            <QueueCount
              queue={item.id}
              stats={stats}
              activeQueue={queue}
              activeTotal={usableList ? data.total : undefined}
              statsError={statsError}
              matching={matching}
            />
            <small>{item.description}</small>
          </button>
        ))}
      </nav>

      {queue === "not_checked" && (
        <Alert title="Check queue" tone="info">
          These manuscripts are prepared and have not been checked yet.
        </Alert>
      )}

      <section className="signal-desk" aria-labelledby="desk-heading">
        <div className="signal-desk__heading">
          <div>
            <p className="signal-eyebrow">Current queue</p>
            <h2 id="desk-heading" ref={queueHeading} tabIndex={-1}>{queueTitle}</h2>
          </div>
          <p role="status" aria-live="polite" aria-atomic="true" aria-busy={loading}>
            {loading ? <span className="sr-only">{loadingMessage}</span> : usableList ? <>
              <span className="sr-only">{queueTitle}: Page {data.page} of {totalPages}. </span><strong>{data.total}</strong>{matching ? " matching" : ""} manuscript{data.total === 1 ? "" : "s"}.
            </> : null}
          </p>
        </div>

        <div className="signal-filters">
          <form role="search" onSubmit={submitSearch}>
            <label htmlFor="review-desk-search">Search group or file in this queue</label>
            <div>
              <input
                id="review-desk-search"
                type="search"
                aria-describedby="review-desk-search-help"
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
              />
              <Button type="submit" variant="secondary">Search</Button>
            </div>
          </form>
          <div className="signal-filter-field">
            <label htmlFor="review-desk-program">Program</label>
            <select
              id="review-desk-program"
              value={program ?? ""}
              onChange={(event) => update({ program: event.target.value || undefined, page: undefined })}
            >
              <option value="">All programs</option>
              {(programs ?? []).map((item) => (
                <option key={item.id} value={item.name}>{item.name}</option>
              ))}
              <option value={UNSET_PROGRAM_FILTER}>Not set</option>
            </select>
          </div>
          <div className="signal-filter-field">
            <label htmlFor="review-desk-sort">Order</label>
            <select
              id="review-desk-sort"
              value={sort}
              onChange={(event) => update({ sort: event.target.value, page: undefined })}
            >
              <option value="needs_review_desc">Needs attention first</option>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="group_asc">Group A to Z</option>
            </select>
          </div>
          {(q || program) && (
            <Button
              variant="quiet"
              onClick={() => {
                setSearchDraft("");
                update({ q: undefined, program: undefined, page: undefined });
              }}
            >
              Clear filters
            </Button>
          )}
          <p id="review-desk-search-help" className="signal-filter-help">
            Only manuscripts in {queueTitle} are searched. Change queues to find other uploads.
          </p>
        </div>

        {loading && (
          <div className="signal-desk-loading" aria-hidden="true">
            <span>{loadingMessage}</span>
            <i /><i /><i />
          </div>
        )}
        {pastEnd && !loading && (
          <Alert title={PAGE_PAST_END_COPY.title(page)} tone="info" role="alert">
            <p>{PAGE_PAST_END_COPY.body}</p>
            <ActionLink to={{ search: (() => { const next = new URLSearchParams(params); next.delete("page"); return next.toString(); })() }} variant="secondary">{PAGE_PAST_END_COPY.firstPage}</ActionLink>
          </Alert>
        )}
        {isError && !pastEnd && !loading && (
          <Alert title={`Could not load page ${page} in ${queueTitle}`} tone="error" role="alert">
            <p>Your records have not changed.</p>
            <Button variant="secondary" onClick={retryQueue}>Try again</Button>
          </Alert>
        )}
        {usableList && data.items.length === 0 && (
          <div className="signal-desk-empty">
            <div className="signal-desk-empty__mark" aria-hidden="true" />
            <h3>{noManuscripts ? "No manuscripts uploaded yet" : q || program ? `No matches in ${queueTitle}` : queue === "not_checked" ? "No unchecked manuscripts" : "This queue is clear"}</h3>
            <p>
              {noManuscripts
                ? "Add a manuscript now, or finish reviewing your required format first."
                : q || program
                ? "Clear the filters or choose another queue."
                : queue === "not_checked"
                  ? "New uploads appear here after preparation. Previously checked manuscripts stay in their other queues."
                  : "There is no manuscript work in this state right now."}
            </p>
            {q || program ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setSearchDraft("");
                  update({ q: undefined, program: undefined, page: undefined });
                }}
              >
                Clear filters
              </Button>
            ) : (
              <Button variant="brand" onClick={onUploadManuscript}>Add a manuscript</Button>
            )}
          </div>
        )}
        {data && data.items.length > 0 && (
          <ul className="signal-record-list" aria-label={`${queueTitle} manuscripts`}>
            {data.items.map((row) => (
              <WorkQueueRecord
                key={row.id}
                row={row}
                onStartCheck={onStartCheck}
                onRerun={onRerun}
                onSetGroup={onSetGroup}
                onUploadReplacement={onUploadManuscript}
              />
            ))}
          </ul>
        )}

        {data && totalPages > 1 && (
          <nav className="signal-pagination" aria-label="Review Desk pages">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={(event) => changePage(event, page - 1)}
            >
              Previous
            </Button>
            <span>Page {page} of {totalPages}</span>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={(event) => changePage(event, page + 1)}
            >
              Next
            </Button>
          </nav>
        )}
      </section>
    </>
  );
}

export function DashboardPage() {
  const familyQuery = useRubricFamilies();
  const { data: families, isPending: familiesPending } = familyQuery;
  const [uploadFormatOpen, setUploadFormatOpen] = useState(false);
  const [uploadManuscriptOpen, setUploadManuscriptOpen] = useState(false);
  const [newCheckOpen, setNewCheckOpen] = useState(false);
  const [preselectManuscriptId, setPreselectManuscriptId] = useState<number>();
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunInitialIds, setRerunInitialIds] = useState<number[]>();
  const [setGroupManuscriptId, setSetGroupManuscriptId] = useState<number>();
  const [deskUsed, setDeskUsed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Review Desk - VERIDICAL", headingRef);

  // BUG-128: a report's integrity-check coverage gap (SignalReport.tsx's
  // `CoverageStatement`) links here with `?rerun=<manuscript_id>` instead
  // of duplicating the rerun mechanism -- this opens the SAME modal the
  // row-level "Run again" button already opens, just reachable one click
  // earlier. Stripped from the URL immediately so it can't re-fire on a
  // later back-navigation or refresh.
  const [rerunParams, setRerunParams] = useSearchParams();
  // Genuinely safe to list both real deps (no suppression comment needed):
  // the first run strips "rerun" from the URL, which changes `rerunParams`
  // and re-fires this effect, but the second run finds `raw === null` and
  // bails immediately -- self-terminating, not a loop.
  useEffect(() => {
    const raw = rerunParams.get("rerun");
    if (raw === null) return;
    const manuscriptId = Number(raw);
    if (Number.isInteger(manuscriptId) && manuscriptId > 0) {
      setRerunInitialIds([manuscriptId]);
      setRerunOpen(true);
    }
    const next = new URLSearchParams(rerunParams);
    next.delete("rerun");
    setRerunParams(next, { replace: true });
  }, [rerunParams, setRerunParams]);
  useEffect(() => {
    const raw = rerunParams.get("check");
    if (raw === null) return;
    const manuscriptId = Number(raw);
    if (Number.isInteger(manuscriptId) && manuscriptId > 0) {
      setPreselectManuscriptId(manuscriptId);
      setNewCheckOpen(true);
    }
    const next = new URLSearchParams(rerunParams);
    next.delete("check");
    setRerunParams(next, { replace: true });
  }, [rerunParams, setRerunParams]);

  const hasActiveRubric = useMemo(
    () => (families ?? []).some((family) => family.is_active),
    [families],
  );
  const presence = useManuscriptsPage(1, REVIEW_DESK_PAGE_SIZE, undefined,
    familyQuery.isSuccess && !hasActiveRubric);
  const confirmedEmpty = presence.isSuccess && !presence.isError && presence.data.total === 0;
  const explicitDeskQuery = ["queue", "q", "program", "sort", "page", "status", "needs_review"]
    .some((key) => rerunParams.has(key));
  const firstUse = !deskUsed && !explicitDeskQuery && familyQuery.isSuccess
    && families?.length === 0 && confirmedEmpty;

  function openCheck(manuscriptId?: number) {
    setPreselectManuscriptId(manuscriptId);
    setNewCheckOpen(true);
  }

  return (
    <div className="signal-route signal-page-flow signal-review-desk">
      <header className="signal-route-header">
        <div>
          <p className="signal-eyebrow">Instructor work queue</p>
          <h1 ref={headingRef} tabIndex={-1}>Review Desk</h1>
          <p className="signal-route-header__intro">
            Find the next manuscript that needs work. VERIDICAL shows evidence and system readiness;
            you review the evidence and record the final decision.
          </p>
        </div>
        <div className="signal-route-actions">
          <Button variant="secondary" onClick={() => setUploadManuscriptOpen(true)}>
            Add manuscript
          </Button>
          <Button variant="brand" data-tour="new-check-cta" onClick={() => openCheck()}>
            Start a check
          </Button>
        </div>
      </header>

      {familiesPending && <p role="status">Checking your required format.</p>}
      {familyQuery.isError && (
        <section className="signal-desk-format-status" aria-labelledby="desk-format-status-heading">
          <div>
            <h2 id="desk-format-status-heading">Required format could not be checked</h2>
            <p>You can still review your manuscripts. Try again to check which format is active.</p>
          </div>
          <Button variant="secondary" onClick={() => void familyQuery.refetch()} disabled={familyQuery.isFetching}>
            {familyQuery.isFetching ? "Checking format…" : "Try again"}
          </Button>
        </section>
      )}
      {firstUse && <FirstUse onUpload={() => setUploadFormatOpen(true)} />}
      {familyQuery.isSuccess && !hasActiveRubric && !firstUse && (
        <section className="signal-desk-format-status" aria-labelledby="desk-format-status-heading">
          <div>
            <h2 id="desk-format-status-heading">{families?.length === 1
              ? "Continue reviewing your required format"
              : families && families.length > 1
                ? "Choose a required format to finish setup"
                : "Add a required format before checking"}</h2>
            {families?.length === 1 && <p><strong>{families[0].title} · Version {families[0].version}</strong> <FormatReviewStateTag rubricId={families[0].id} /></p>}
            <p>{families?.length === 1
              ? "Review and confirm the prepared criteria before starting a check."
              : families && families.length > 1
                ? "Review a prepared format and activate the version you want to use for new checks."
                : presence.isSuccess && presence.data.total > 0
                  ? "Your uploaded manuscripts remain available below. Upload your program’s format, then review the prepared criteria."
                  : "Upload your program’s format, then review the prepared criteria. Any existing manuscripts can still be reviewed below."}</p>
          </div>
          {families?.length === 1
            ? <ActionLink to={`/rubric/${families[0].id}/review`}>Continue review</ActionLink>
            : families && families.length > 1
              ? <ActionLink to="/rubric">Manage required formats</ActionLink>
              : <Button variant="brand" data-tour="upload-format-cta" onClick={() => setUploadFormatOpen(true)}>Upload required format</Button>}
        </section>
      )}
      <div hidden={firstUse} onFocusCapture={() => setDeskUsed(true)} onPointerDownCapture={() => setDeskUsed(true)} onKeyDownCapture={() => setDeskUsed(true)}>
        <ReviewDesk
          noManuscripts={confirmedEmpty && !hasActiveRubric}
          onUploadManuscript={() => setUploadManuscriptOpen(true)}
          onStartCheck={openCheck}
          onRerun={(id) => {
            setRerunInitialIds([id]);
            setRerunOpen(true);
          }}
          onSetGroup={setSetGroupManuscriptId}
        />
      </div>

      {newCheckOpen && (
        <NewCheckModal
          initialManuscriptId={preselectManuscriptId}
          onUploadManuscript={() => {
            setNewCheckOpen(false);
            setUploadManuscriptOpen(true);
          }}
          onClose={() => {
            setNewCheckOpen(false);
            setPreselectManuscriptId(undefined);
          }}
        />
      )}
      {uploadFormatOpen && <UploadRubricModal onClose={() => setUploadFormatOpen(false)} />}
      {uploadManuscriptOpen && (
        <UploadManuscriptModal
          onClose={(createdManuscriptId) => {
            setUploadManuscriptOpen(false);
            if (createdManuscriptId !== undefined) {
              setRerunParams({ queue: "not_checked", status: "not_checked", sort: "newest" });
            }
          }}
          onUploadSuccess={(id) => {
            setUploadManuscriptOpen(false);
            openCheck(id);
          }}
        />
      )}
      {rerunOpen && (
        <RerunModal
          initialManuscriptIds={rerunInitialIds}
          onClose={() => {
            setRerunOpen(false);
            setRerunInitialIds(undefined);
          }}
        />
      )}
      {setGroupManuscriptId !== undefined && (
        <GroupProposalDialog
          manuscriptId={setGroupManuscriptId}
          onClose={() => setSetGroupManuscriptId(undefined)}
          onDone={() => setSetGroupManuscriptId(undefined)}
        />
      )}
    </div>
  );
}
