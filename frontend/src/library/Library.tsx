// Screen 4w-List (V-066) — browse every manuscript VERIDICAL has ever
// processed, across every instructor account (BUG-050 Branch B: the
// corpus is shared by design). Replaces the old Archive screen (4t): a
// strict superset -- every own manuscript still gets its archive state and
// purge action here, plus every other account's, plus real metadata
// (title/authors/program) Archive never showed.
import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { LibraryItemOut } from "../api/types";
import { Chip } from "../components/Chip";
import { Modal, ModalBackdrop } from "../components/Modal";
import { StatusPill } from "../components/StatusPill";
import { usePrograms } from "../dashboard/useDashboard";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useLibrary, usePurgeManuscript } from "./useLibrary";

// Must match `app/groups/service.py::UNSET_PROGRAM_FILTER` exactly (same
// protocol-only literal `ManuscriptsTable.tsx`'s own filter already uses).
const UNSET_PROGRAM_FILTER = "__unset__";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function libraryIdentity(item: LibraryItemOut) {
  if (item.title) return { primary: item.title, secondary: item.group_label };
  return manuscriptIdentity(item.group_label, item.original_filename);
}

function ProgramFilter({
  program,
  onChange,
}: {
  program: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const { data: programs, isLoading } = usePrograms();
  const filterId = useId();
  if (isLoading) return null;
  if (!Array.isArray(programs) || programs.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
      <label htmlFor={filterId} className="text-sm font-medium text-ink">
        Filter by program
      </label>
      <select
        id={filterId}
        value={program ?? ""}
        onChange={(event) => onChange(event.target.value || undefined)}
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

function ArchiveStatePill({ purgedAt }: { purgedAt: string | null }) {
  // newcomer finding (V-066 review, 2026-08-23): "Archived" alone reads
  // as a readiness/pipeline status (this app already uses "Checked,"
  // "Approved for defense," etc. elsewhere) rather than what it actually
  // means -- retention. "Content stored" parallels "Content removed"
  // directly and doesn't depend on a column header (only present on the
  // desktop table, not the mobile card) to disambiguate it.
  if (purgedAt) return <StatusPill tone="neutral">Content removed</StatusPill>;
  return <StatusPill tone="success">Content stored</StatusPill>;
}

// ux-critic finding (V-066 review, 2026-08-23): the mobile card and
// desktop table used to word this pill differently ("Another instructor's"
// vs. "Another's") for the identical fact -- one shared component so the
// two renderings can't drift again, the same class of bug this project
// has hit before with duplicated responsive markup (V-055).
function WhosePill({ isOwn }: { isOwn: boolean }) {
  return (
    <StatusPill tone={isOwn ? "info" : "neutral"}>{isOwn ? "Yours" : "Another instructor's"}</StatusPill>
  );
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

// ux-critic finding (V-066 review, 2026-08-23): the "Whose" column was
// 120px, sized for the desktop table's own shorter "Another's" label --
// which is exactly the copy inconsistency the same finding flagged
// (mobile said "Another instructor's" for the identical fact). Widened
// to fit the one shared string both layouts now use.
const gridCols = "grid-cols-[28px_minmax(0,2fr)_minmax(0,1.2fr)_100px_90px_160px_120px_110px]";

function RowActions({
  item,
  onPurge,
}: {
  item: LibraryItemOut;
  onPurge: (target: PurgeTarget) => void;
}) {
  const identity = libraryIdentity(item);
  return (
    // ux-critic finding (V-066 review, 2026-08-23): measured live at
    // 29.8x18/33.5x18 -- under WCAG 2.5.8's 24x24 CSS-px minimum, and the
    // 12px gap doesn't qualify for the spacing-offset exception either.
    // `min-h-6` (24px) unconditionally, not gated behind `sm:` (a real
    // Archive.tsx-inherited mistake this measurement caught), plus enough
    // gap to clear the exception on its own.
    <div className="flex items-center gap-4">
      <Link
        to={`/library/${item.manuscript_id}`}
        className="inline-flex min-h-6 items-center text-xs font-medium text-link underline hover:text-link-hover"
      >
        Open
      </Link>
      {item.is_own && !item.purged_at && (
        <button
          type="button"
          onClick={() => onPurge({ manuscriptId: item.manuscript_id, primary: identity.primary })}
          className="inline-flex min-h-6 items-center text-xs font-medium text-danger underline hover:text-danger-hover"
        >
          Purge
        </button>
      )}
    </div>
  );
}

export function LibraryPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Library - VERIDICAL", headingRef);
  const navigate = useNavigate();

  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number(searchParams.get("page") ?? "1");
  const program = searchParams.get("program") ?? undefined;
  const urlSearch = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(urlSearch);

  // Debounced commit of the search box into the URL (same setTimeout
  // pattern GroupProposalFields.tsx's own collision check already uses).
  useEffect(() => {
    const handle = setTimeout(() => {
      const next = new URLSearchParams(searchParams);
      if (searchInput.trim()) next.set("q", searchInput.trim());
      else next.delete("q");
      next.set("page", "1");
      setSearchParams(next, { replace: true });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, 400);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const { data, isLoading, isError, refetch } = useLibrary(page, program, urlSearch);
  const purge = usePurgeManuscript();
  const [purgeTarget, setPurgeTarget] = useState<PurgeTarget | null>(null);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  // Detail's "Compare with..." link pre-fills one compare slot rather than
  // dropping the instructor back at an unpopulated list -- read once on
  // mount, then the param is dropped so it doesn't re-fire on every
  // re-render or reappear after Clear.
  const [selected, setSelected] = useState<number[]>(() => {
    const from = searchParams.get("compareFrom");
    return from ? [Number(from)] : [];
  });
  useEffect(() => {
    if (!searchParams.has("compareFrom")) return;
    const next = new URLSearchParams(searchParams);
    next.delete("compareFrom");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setProgram(next: string | undefined) {
    const params = new URLSearchParams(searchParams);
    if (next) params.set("program", next);
    else params.delete("program");
    params.set("page", "1");
    setSearchParams(params);
  }

  function toggleSelect(manuscriptId: number) {
    setSelected((prev) => {
      if (prev.includes(manuscriptId)) return prev.filter((id) => id !== manuscriptId);
      if (prev.length >= 2) return prev;
      return [...prev, manuscriptId];
    });
  }

  async function confirmPurge() {
    if (!purgeTarget) return;
    setPurgeError(null);
    try {
      await purge.mutateAsync(purgeTarget.manuscriptId);
      setPurgeTarget(null);
    } catch (err) {
      setPurgeError(
        err instanceof ApiError ? err.message : "Could not remove this manuscript's content. Try again.",
      );
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const hasFilters = Boolean(program) || Boolean(urlSearch.trim());
  const trulyEmpty = data ? data.total === 0 && !hasFilters : false;
  const filteredToZero = data ? data.items.length === 0 && hasFilters : false;

  const selectedItems = data
    ? selected
        .map((id) => data.items.find((i) => i.manuscript_id === id))
        .filter((i): i is LibraryItemOut => i !== undefined)
    : [];

  return (
    <div className="flex flex-col gap-4 p-4 pb-24 sm:p-6">
      {/* ux-critic finding (V-066 review, 2026-08-23): the heading used to
          be mounted only AFTER loading/error early-returns, so `useRouteFocus`'s
          one focus attempt (fired on the FIRST effect run, before data
          arrives) always found a null ref and silently no-op'd -- this
          screen never actually moved focus on navigation, unlike
          DocumentViewerPage, whose heading is unconditional for the same
          reason. Always rendering the heading first fixes both screens
          this bug was found on (Library, LibraryDetail). */}
      <div>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink">
          Library
        </h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Browse every manuscript VERIDICAL has processed across every instructor account. This is
          a shared library, so a match in one of your reports can reference a manuscript below that
          you never uploaded yourself.
        </p>
        <p className="mt-1 text-sm text-ink-secondary">
          Filtering and comparing work the same whether a manuscript is yours or another
          instructor's. Content does not: yours is always fully readable, another instructor's is a
          bounded excerpt only.
        </p>
      </div>

      {isLoading && (
        <div role="status" aria-live="polite" aria-busy="true" className="p-4 text-sm text-ink-tertiary">
          Loading library.
        </div>
      )}

      {(isError || (!isLoading && !data)) && (
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
        >
          Could not load the library.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
          .
        </div>
      )}

      {data && (
        <>
      {/* newcomer finding (V-066 review, 2026-08-23): `flex-wrap` alone
          never wrapped this row -- a `flex-1 min-w-0` input just keeps
          shrinking to fit instead, and at 390px it shrank until its own
          placeholder text ("Search title, author, or group") clipped to
          "Searc". Stacking below `sm:` (same convention `ProgramFilter`
          already uses) gives the input real width instead. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <Chip>{data.total} processed</Chip>
        <input
          type="text"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search title, author, or group"
          aria-label="Search the library"
          className="h-11 min-w-0 rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9 sm:max-w-xs sm:flex-1"
        />
        <ProgramFilter program={program} onChange={setProgram} />
      </div>

      {trulyEmpty && (
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
          <h3 className="text-md font-bold text-ink">Nothing in the library yet</h3>
          <p className="max-w-md text-sm text-ink-secondary">
            Manuscripts appear here once any instructor uploads and checks one. VERIDICAL's shared
            library grows as more manuscripts are processed across every account.
          </p>
          <Link to="/dashboard" className="mt-1 text-sm font-medium text-link underline hover:text-link-hover">
            Go to Dashboard
          </Link>
        </div>
      )}

      {filteredToZero && (
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
          <h3 className="text-md font-bold text-ink">No manuscripts match this filter</h3>
          <button
            type="button"
            onClick={() => {
              setSearchInput("");
              setSearchParams({});
            }}
            className="text-sm font-medium text-link underline hover:text-link-hover"
          >
            Clear filters
          </button>
        </div>
      )}

      {!trulyEmpty && !filteredToZero && (
        <>
          <ul className="flex flex-col gap-2 lg:hidden">
            {data.items.map((item) => {
              const identity = libraryIdentity(item);
              return (
                <li key={item.manuscript_id} className="rounded-lg border border-border bg-panel p-3">
                  <div className="flex items-start justify-between gap-2">
                    <label className="flex min-w-0 items-start gap-2">
                      <input
                        type="checkbox"
                        checked={selected.includes(item.manuscript_id)}
                        disabled={!selected.includes(item.manuscript_id) && selected.length >= 2}
                        onChange={() => toggleSelect(item.manuscript_id)}
                        aria-label={`Add ${identity.primary} to compare`}
                        // ux-critic finding (V-066 review, 2026-08-23):
                        // this attribute existed on the desktop table's
                        // checkbox only -- a screen-reader user below the
                        // `lg` breakpoint heard "disabled" with no reason
                        // why. Same duplicated-responsive-markup class of
                        // bug this codebase has hit before (V-055).
                        aria-describedby={
                          !selected.includes(item.manuscript_id) && selected.length >= 2
                            ? "compare-limit-note"
                            : undefined
                        }
                        className="mt-1 h-5 w-5 flex-none"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink" title={identity.primary}>
                          {identity.primary}
                        </span>
                        {identity.secondary && (
                          <span className="block truncate text-xs text-ink-tertiary">{identity.secondary}</span>
                        )}
                      </span>
                    </label>
                    <WhosePill isOwn={item.is_own} />
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs text-ink-tertiary">
                    <span className="flex flex-wrap items-center gap-2">
                      <span>{item.program ?? "Not set"}</span>
                      <span>{formatDate(item.created_at)}</span>
                      <ArchiveStatePill purgedAt={item.purged_at} />
                    </span>
                    <RowActions item={item} onPurge={setPurgeTarget} />
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="hidden overflow-x-auto rounded-lg border border-border lg:block">
            <div
              role="row"
              className={`grid ${gridCols} min-w-[900px] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase`}
            >
              <span role="columnheader">
                <span className="sr-only">Compare</span>
              </span>
              <span role="columnheader">Manuscript</span>
              <span role="columnheader">Authors</span>
              <span role="columnheader">Program</span>
              <span role="columnheader">Ingested</span>
              <span role="columnheader">Whose</span>
              <span role="columnheader">Archive</span>
              <span role="columnheader">
                <span className="sr-only">Actions</span>
              </span>
            </div>
            {data.items.map((item) => {
              const identity = libraryIdentity(item);
              const disabled = !selected.includes(item.manuscript_id) && selected.length >= 2;
              return (
                <div
                  key={item.manuscript_id}
                  role="row"
                  className={`grid ${gridCols} min-w-[900px] items-center gap-3 border-t border-border px-4 py-3 text-sm`}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(item.manuscript_id)}
                    disabled={disabled}
                    onChange={() => toggleSelect(item.manuscript_id)}
                    aria-label={`Add ${identity.primary} to compare`}
                    aria-describedby={disabled ? "compare-limit-note" : undefined}
                    className="h-5 w-5"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-ink" title={identity.primary}>
                      {identity.primary}
                    </span>
                    {identity.secondary && (
                      <span className="block truncate text-xs text-ink-tertiary" title={identity.secondary}>
                        {identity.secondary}
                      </span>
                    )}
                  </span>
                  <span className="truncate text-ink-secondary" title={item.authors.join(", ")}>
                    {item.authors.length > 0 ? item.authors.join(", ") : "Not listed"}
                  </span>
                  <span className="text-ink-tertiary">{item.program ?? "Not set"}</span>
                  <span className="text-ink-tertiary">{formatDate(item.created_at)}</span>
                  <WhosePill isOwn={item.is_own} />
                  <ArchiveStatePill purgedAt={item.purged_at} />
                  <RowActions item={item} onPurge={setPurgeTarget} />
                </div>
              );
            })}
          </div>
          <p id="compare-limit-note" className="sr-only">
            Comparing two at a time. Remove a selected manuscript first.
          </p>
        </>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setSearchParams({ ...Object.fromEntries(searchParams), page: String(page - 1) })}
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
            onClick={() => setSearchParams({ ...Object.fromEntries(searchParams), page: String(page + 1) })}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Next
          </button>
        </div>
      )}
        </>
      )}

      {selected.length > 0 && (
        <div
          role="region"
          aria-label="Comparison selection"
          className="fixed inset-x-0 bottom-0 z-raised flex flex-wrap items-center justify-between gap-2 border-t border-border bg-panel px-4 py-3 shadow-sm sm:px-6"
        >
          <p className="text-sm text-ink">
            {selectedItems.length === selected.length ? (
              <>
                {selectedItems.map((item, i) => (
                  <span key={item.manuscript_id}>
                    {i > 0 && " and "}
                    <b>{libraryIdentity(item).primary}</b>
                  </span>
                ))}{" "}
                selected.
              </>
            ) : (
              // A selection carried over from Detail's "Compare with..."
              // link (compareFrom) may not be on the current page/filter --
              // still comparable, just not nameable without a second fetch.
              <>{selected.length} manuscript{selected.length > 1 ? "s" : ""} selected.</>
            )}
            {selected.length === 1 && " Choose a second manuscript to compare."}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSelected([])}
              className="text-sm font-medium text-link underline hover:text-link-hover"
            >
              Clear
            </button>
            <button
              type="button"
              disabled={selected.length !== 2}
              onClick={() => navigate(`/library/compare?a=${selected[0]}&b=${selected[1]}`)}
              className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-45 sm:h-9"
            >
              Compare
            </button>
          </div>
        </div>
      )}

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
