// V-073 screen 4b/4e — Signal Review Desk. The page is a prioritized work
// queue, not an analytics dashboard: each record names what happened, what
// still needs the instructor, and one honest next action.
import { type FormEvent, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { ManuscriptListItem } from "../api/types";
import { GroupProposalDialog } from "../check/GroupProposalDialog";
import { NewCheckModal } from "../check/NewCheck";
import { UploadManuscriptModal } from "../check/UploadManuscriptModal";
import { REVIEW_DESK_PAGE_SIZE, UNSET_PROGRAM_FILTER } from "../config/ui";
import {
  type ReviewDeskQuery,
  type ReviewDeskQueue,
  type ReviewDeskSort,
  useDismissFailedManuscript,
  useReviewDeskPage,
} from "../dashboard/useReviewDesk";
import { useDashboardStats, usePrograms } from "../dashboard/useDashboard";
import { DECISION_LABEL } from "../domain/decisionTone";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { RerunModal } from "../rubric/RerunModal";
import { UploadRubricModal } from "../rubric/UploadRubricModal";
import { useRubricFamilies } from "../rubric/useRubric";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ProcessStatus } from "../ui/ProcessStatus";
import { ReadinessBand } from "../ui/ReadinessBand";

const QUEUES: ReadonlyArray<{
  id: ReviewDeskQueue;
  label: string;
  description: string;
}> = [
  { id: "needs_review", label: "Needs you", description: "Unresolved or recovery work" },
  { id: "checking", label: "In progress", description: "Checks currently running" },
  {
    id: "ready_to_decide",
    label: "Ready to decide",
    description: "Assessed manuscripts awaiting your outcome",
  },
  { id: "complete", label: "Complete", description: "Instructor decisions already recorded" },
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
}: {
  queue: ReviewDeskQueue;
  stats?: ReturnType<typeof useDashboardStats>["data"];
  activeQueue: ReviewDeskQueue;
  activeTotal?: number;
}) {
  if (queue === activeQueue && activeTotal !== undefined) {
    return <span>{activeTotal} manuscript{activeTotal === 1 ? "" : "s"}</span>;
  }
  if (!stats) return null;
  let count: number | null = null;
  if (queue === "ready_to_decide") {
    count = Math.max(
      0,
      stats.manuscripts_checked - stats.needs_review_count - stats.decided_count,
    );
  }
  if (queue === "complete") count = stats.decided_count;
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
        Start with the rubric or format document your program requires. VERIDICAL prepares
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
  if (row.ingest_status === "failed") {
    return <Button variant="secondary" onClick={onUploadReplacement}>Try a corrected file</Button>;
  }
  if (row.latest_check_run_status === "done" && row.latest_check_run_id) {
    const label = row.escalations_awaiting_review > 0
      ? `Review ${row.escalations_awaiting_review} unresolved ${row.escalations_awaiting_review === 1 ? "criterion" : "criteria"}`
      : row.latest_decision
        ? "Open recorded decision"
        : "Record instructor decision";
    return <ActionLink to={`/report/${row.latest_check_run_id}`}>{label}</ActionLink>;
  }
  if (row.latest_check_run_status && row.latest_check_run_id) {
    if (row.latest_check_run_status === "cancelled") {
      return <Button onClick={() => onStartCheck(row.id)}>Start a fresh check</Button>;
    }
    return (
      <ActionLink to={`/checks/${row.latest_check_run_id}`}>
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
  const failureCopy = row.ingest_failure_reason
    ? FAILURE_COPY[row.ingest_failure_reason]
    : "This file could not be prepared. A specific reason was not recorded.";

  return (
    <li className="signal-record">
      <div className="signal-record__identity">
        <h3>{identity.primary}</h3>
        {identity.secondary && <p title={identity.secondary}>{identity.secondary}</p>}
        <dl>
          <div><dt>Program</dt><dd>{row.program ?? "Not set"}</dd></div>
          <div><dt>Last activity</dt><dd>{formatActivity(row.created_at)}</dd></div>
        </dl>
      </div>
      <div className="signal-record__state">
        <span className="signal-record__label">Process</span>
        <ProcessStatus status={recordProcess(row)} />
        {row.ingest_status === "failed" && <p>{failureCopy}</p>}
      </div>
      <div className="signal-record__assessment">
        <span className="signal-record__label">System readiness</span>
        {row.latest_readiness
          ? <ReadinessBand status={row.latest_readiness} />
          : <span className="signal-record__not-available">Not available yet</span>}
        {row.escalations_awaiting_review > 0 && (
          <p>
            <strong>{row.escalations_awaiting_review}</strong>{" "}
            {row.escalations_awaiting_review === 1 ? "criterion task needs" : "criterion tasks need"} you
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
        <details>
          <summary>More actions</summary>
          <div>
            {row.ingest_status === "done" && (
              <button type="button" onClick={() => onSetGroup(row.id)}>Review group details</button>
            )}
            {row.latest_done_check_run_id && row.latest_done_check_run_id !== row.latest_check_run_id && (
              <Link to={`/report/${row.latest_done_check_run_id}`}>Open prior report</Link>
            )}
            {row.latest_done_check_run_id ? (
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
            )}
            {row.ingest_status === "failed" && (
              <button
                type="button"
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(row.id)}
              >
                {dismiss.isPending ? "Moving…" : "Move to Archive"}
              </button>
            )}
          </div>
        </details>
        {row.ingest_status === "failed" && (
          <p className="signal-record__retention">Archive retains the failed record and its audit history.</p>
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
}: {
  onUploadManuscript: () => void;
  onStartCheck: (id: number) => void;
  onRerun: (id: number) => void;
  onSetGroup: (id: number) => void;
}) {
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
  const query: ReviewDeskQuery = { queue, q, program, sort, page, pageSize: REVIEW_DESK_PAGE_SIZE };
  const { data, isPending, isError, refetch } = useReviewDeskPage(query);
  const { data: stats } = useDashboardStats();
  const { data: programs } = usePrograms();

  function update(next: Record<string, string | undefined>, replace = false) {
    const updated = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) updated.set(key, value);
      else updated.delete(key);
    }
    setParams(updated, { replace });
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
  const queueTitle = queue === "not_checked"
    ? "Ready to check"
    : QUEUES.find((item) => item.id === queue)?.label ?? "Needs you";

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
              activeTotal={data?.total}
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
            <h2 id="desk-heading">{queueTitle}</h2>
          </div>
          {data && (
            <p aria-live="polite">
              <strong>{data.total}</strong> manuscript{data.total === 1 ? "" : "s"}
            </p>
          )}
        </div>

        <div className="signal-filters">
          <form role="search" onSubmit={submitSearch}>
            <label htmlFor="review-desk-search">Search group or file</label>
            <div>
              <input
                id="review-desk-search"
                type="search"
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
        </div>

        {isPending && (
          <div className="signal-desk-loading" role="status" aria-busy="true">
            <span>Loading {queueTitle.toLowerCase()} manuscripts…</span>
            <i /><i /><i />
          </div>
        )}
        {isError && (
          <Alert title="Could not load this queue" tone="error" role="alert">
            <p>Your records have not changed.</p>
            <Button variant="secondary" onClick={() => refetch()}>Try again</Button>
          </Alert>
        )}
        {data && data.items.length === 0 && (
          <div className="signal-desk-empty">
            <div className="signal-desk-empty__mark" aria-hidden="true" />
            <h3>{q || program ? "No manuscripts match these filters" : "This queue is clear"}</h3>
            <p>
              {q || program
                ? "Clear or change a filter to widen the view."
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
              onClick={() => update({ page: String(page - 1) })}
            >
              Previous
            </Button>
            <span>Page {page} of {totalPages}</span>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => update({ page: String(page + 1) })}
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
  const { data: families, isPending: familiesPending } = useRubricFamilies();
  const [uploadFormatOpen, setUploadFormatOpen] = useState(false);
  const [uploadManuscriptOpen, setUploadManuscriptOpen] = useState(false);
  const [newCheckOpen, setNewCheckOpen] = useState(false);
  const [preselectManuscriptId, setPreselectManuscriptId] = useState<number>();
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunInitialIds, setRerunInitialIds] = useState<number[]>();
  const [setGroupManuscriptId, setSetGroupManuscriptId] = useState<number>();
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Review Desk - VERIDICAL", headingRef);

  const hasActiveRubric = useMemo(
    () => (families ?? []).some((family) => family.is_active),
    [families],
  );

  function openCheck(manuscriptId?: number) {
    setPreselectManuscriptId(manuscriptId);
    setNewCheckOpen(true);
  }

  return (
    <div className="signal-route signal-review-desk">
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

      {familiesPending ? (
        <div className="signal-desk-loading" role="status" aria-busy="true">
          <span>Preparing your Review Desk…</span><i /><i />
        </div>
      ) : hasActiveRubric ? (
        <ReviewDesk
          onUploadManuscript={() => setUploadManuscriptOpen(true)}
          onStartCheck={openCheck}
          onRerun={(id) => {
            setRerunInitialIds([id]);
            setRerunOpen(true);
          }}
          onSetGroup={setSetGroupManuscriptId}
        />
      ) : (
        <FirstUse onUpload={() => setUploadFormatOpen(true)} />
      )}

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
          onClose={() => setUploadManuscriptOpen(false)}
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
