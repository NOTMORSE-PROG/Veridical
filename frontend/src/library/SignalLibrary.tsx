import { useEffect, useId, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { LibraryItemOut } from "../api/types";
import { LIBRARY_SEARCH_DEBOUNCE_MS, UNSET_PROGRAM_FILTER } from "../config/ui";
import { usePrograms } from "../dashboard/useDashboard";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import {
  useLibrary,
  useLibraryDocument,
  useLibraryExcerpt,
  useLibraryItem,
  useLibraryParagraphs,
  usePurgeManuscript,
} from "./useLibrary";

interface PurgeTarget {
  manuscriptId: number;
  title: string;
}

function identity(item: LibraryItemOut) {
  return item.title
    ? { primary: item.title, secondary: item.group_label }
    : manuscriptIdentity(item.group_label, item.original_filename);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

type ComparisonAvailabilityStatus = "checking" | "available" | "unavailable" | "error";

interface ComparisonAvailability {
  manuscriptId: number | undefined;
  label: string;
  status: ComparisonAvailabilityStatus;
  retry: () => void;
}

function selectionStatus(selections: ComparisonAvailability[]): string {
  if (selections.length === 0) return "No manuscripts selected. Select two manuscripts.";
  const hasUnavailable = selections.some((selection) => selection.status === "unavailable");
  const hasError = selections.some((selection) => selection.status === "error");
  const isChecking = selections.some((selection) => selection.status === "checking");
  if (selections.length === 1) {
    if (hasUnavailable) return "The selected manuscript has no viewable comparison content. Clear it and choose another.";
    if (hasError) return "The selected manuscript's availability could not be checked. Try again before choosing another.";
    if (isChecking) return "One manuscript selected. Checking comparison content.";
    return "One manuscript selected and available. Select one more manuscript.";
  }
  if (hasUnavailable) return "At least one selected manuscript has no viewable comparison content. Clear it and choose another.";
  if (hasError) return "One or more selected manuscripts could not be checked. Try again before comparing.";
  if (isChecking) return "Two manuscripts selected. Checking comparison content.";
  return "Two manuscripts selected and available. You can compare them now.";
}

function availabilityLabel(status: ComparisonAvailabilityStatus): string {
  if (status === "checking") return "Checking comparison content";
  if (status === "available") return "Available to compare";
  if (status === "unavailable") return "No viewable comparison content";
  return "Could not check availability";
}

function selectedRecordLabel(item: LibraryItemOut | undefined, manuscriptId: number | undefined): string {
  if (!item) return manuscriptId ? `Manuscript record ${manuscriptId}` : "Manuscript record";
  const label = identity(item);
  return [label.primary, label.secondary, formatDate(item.created_at)].filter(Boolean).join(" · ");
}

function useComparisonAvailability(
  manuscriptId: number | undefined,
  listedItem: LibraryItemOut | undefined,
): ComparisonAvailability {
  // A selected item normally comes from the already-loaded page. Only a
  // compareFrom deep link may require one bounded metadata request.
  const itemQuery = useLibraryItem(manuscriptId, Boolean(manuscriptId && !listedItem));
  const item = listedItem ?? itemQuery.data;
  const canReadContent = Boolean(item && !item.purged_at);
  const documentQuery = useLibraryDocument(
    manuscriptId,
    canReadContent && item?.is_own === true,
  );
  const excerptQuery = useLibraryExcerpt(
    manuscriptId,
    canReadContent && item?.is_own === false,
  );
  const paragraphsQuery = useLibraryParagraphs(
    manuscriptId,
    canReadContent
      && item?.is_own === true
      && documentQuery.data?.available === true
      && documentQuery.data.source_format === "docx",
  );

  let status: ComparisonAvailabilityStatus = "checking";
  if (itemQuery.isError && !listedItem) {
    status = "error";
  } else if (item) {
    if (item.purged_at) {
      status = "unavailable";
    } else if (item.is_own) {
      if (documentQuery.isError) status = "error";
      else if (documentQuery.data) {
        if (!documentQuery.data.available) {
          status = "unavailable";
        } else if (documentQuery.data.source_format === "pdf") {
          status = "available";
        } else if (documentQuery.data.source_format === "docx") {
          if (paragraphsQuery.isError) {
            status = paragraphsQuery.error instanceof ApiError && paragraphsQuery.error.status === 410
              ? "unavailable"
              : "error";
          } else if (paragraphsQuery.data) {
            status = paragraphsQuery.data.paragraphs.some((paragraph) => Boolean(paragraph.text.trim()))
              ? "available"
              : "unavailable";
          }
        } else {
          status = "unavailable";
        }
      }
    } else {
      if (excerptQuery.isError) status = "error";
      else if (excerptQuery.data) {
        status = excerptQuery.data.chapters.some((chapter) => Boolean(chapter.excerpt?.trim()))
          ? "available"
          : "unavailable";
      }
    }
  }

  return {
    manuscriptId,
    label: selectedRecordLabel(item, manuscriptId),
    status,
    retry: () => {
      if (!item) void itemQuery.refetch();
      else if (
        item.is_own
        && documentQuery.data?.available === true
        && documentQuery.data.source_format === "docx"
      ) void paragraphsQuery.refetch();
      else if (item.is_own) void documentQuery.refetch();
      else void excerptQuery.refetch();
    },
  };
}

// BUG-187: repeated checkboxes need a unique, record-specific accessible name;
// the visible metadata is the privacy-safe disambiguator shared by sighted and
// screen-reader users.
function accessibleComparisonChoiceName(
  item: LibraryItemOut,
  items: LibraryItemOut[],
  selected: boolean,
): string {
  const label = identity(item);
  const matches = items.filter((candidate) => identity(candidate).primary === label.primary);
  let disambiguator = "";
  if (matches.length > 1) {
    const uploaded = formatDate(item.created_at);
    const sameDate = matches.filter((candidate) => formatDate(candidate.created_at) === uploaded);
    if (sameDate.length === 1) {
      disambiguator = `, uploaded ${uploaded}`;
    } else {
      const group = label.secondary ?? item.group_label;
      const sameGroup = sameDate.filter(
        (candidate) => (identity(candidate).secondary ?? candidate.group_label) === group,
      );
      if (sameGroup.length === 1) {
        disambiguator = `, ${group}, uploaded ${uploaded}`;
      } else {
        const authors = item.authors.length ? item.authors.join(", ") : "authors not listed";
        const sameAuthors = sameGroup.filter((candidate) =>
          (candidate.authors.length ? candidate.authors.join(", ") : "authors not listed") === authors
        );
        disambiguator = sameAuthors.length === 1
          ? `, ${group}, ${authors}, uploaded ${uploaded}`
          : `, processed ${formatDateTime(item.created_at)}`;
      }
    }
  }
  const action = selected ? "Remove" : "Select";
  return `${action} “${label.primary}”${disambiguator} ${selected ? "from" : "for"} comparison`;
}

function ProgramFilter({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const id = useId();
  const { data, isLoading } = usePrograms();
  if (isLoading || !data?.length) return null;

  return (
    <label className="signal-library-filter" htmlFor={id}>
      <span>Program</span>
      <select id={id} value={value ?? ""} onChange={(event) => onChange(event.target.value || undefined)}>
        <option value="">All programs</option>
        {data.map((program) => (
          <option key={program.id} value={program.name}>
            {program.name}
          </option>
        ))}
        <option value={UNSET_PROGRAM_FILTER}>Not set</option>
      </select>
    </label>
  );
}

function LibraryCard({
  item,
  compareMode,
  selected,
  selectionFull,
  accessibleSelectionName,
  showProcessedTime,
  onToggle,
  onPurge,
}: {
  item: LibraryItemOut;
  compareMode: boolean;
  selected: boolean;
  selectionFull: boolean;
  accessibleSelectionName: string;
  showProcessedTime: boolean;
  onToggle: () => void;
  onPurge: (target: PurgeTarget) => void;
}) {
  const label = identity(item);
  const disabled = !selected && selectionFull;

  return (
    <li className="signal-library-card">
      <div className="signal-library-card__rail" aria-hidden="true" />
      <div className="signal-library-card__body">
        <div className="signal-library-card__topline">
          <div className="signal-library-badges">
            <span className="signal-library-badge" data-tone={item.is_own ? "own" : "shared"}>
              {item.is_own ? "Your manuscript" : "Shared excerpt"}
            </span>
            <span className="signal-library-badge" data-tone={item.purged_at ? "removed" : "stored"}>
              {item.purged_at ? "Content removed" : "Content stored"}
            </span>
          </div>
          {compareMode && (
            <label className="signal-library-select" data-disabled={disabled || undefined}>
              <input
                type="checkbox"
                checked={selected}
                aria-label={accessibleSelectionName}
                aria-disabled={disabled || undefined}
                aria-describedby={disabled ? "signal-compare-limit" : undefined}
                onChange={() => {
                  if (!disabled) onToggle();
                }}
              />
              <span>{selected ? "Selected" : "Select"}</span>
            </label>
          )}
        </div>

        <div className="signal-library-card__identity">
          <h2>{label.primary}</h2>
          {label.secondary && <p>{label.secondary}</p>}
        </div>

        <dl className="signal-library-metadata">
          <div>
            <dt>Program</dt>
            <dd>{item.program ?? "Not set"}</dd>
          </div>
          <div>
            <dt>Authors</dt>
            <dd>
              {item.authors.length
                ? item.authors.join(", ")
                : item.is_own
                  ? "Not listed"
                  : "Withheld for another instructor's manuscript"}
            </dd>
          </div>
          <div>
            <dt>Processed</dt>
            <dd>{showProcessedTime ? formatDateTime(item.created_at) : formatDate(item.created_at)}</dd>
          </div>
        </dl>

        <div className="signal-library-card__actions">
          <ActionLink to={`/library/${item.manuscript_id}`} variant="secondary">
            Open record
          </ActionLink>
          {item.is_own && !item.purged_at && (
            <Button
              type="button"
              variant="quiet"
              onClick={() => onPurge({ manuscriptId: item.manuscript_id, title: label.primary })}
            >
              Remove stored content
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

export function SignalLibraryPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Manuscript Library - VERIDICAL", headingRef);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const parsedPage = Number(searchParams.get("page") ?? "1");
  const page = Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const program = searchParams.get("program") ?? undefined;
  const urlSearch = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(urlSearch);
  const compareFrom = Number(searchParams.get("compareFrom"));
  const initialSelection = Number.isInteger(compareFrom) && compareFrom > 0 ? [compareFrom] : [];
  const [compareMode, setCompareMode] = useState(initialSelection.length > 0);
  const [selected, setSelected] = useState<number[]>(initialSelection);
  const [purgeTarget, setPurgeTarget] = useState<PurgeTarget | null>(null);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const purge = usePurgeManuscript();

  useEffect(() => {
    if (!searchParams.has("compareFrom")) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("compareFrom");
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        const query = searchInput.trim();
        if (query) next.set("q", query);
        else next.delete("q");
        next.set("page", "1");
        return next;
      }, { replace: true });
    }, LIBRARY_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [searchInput, setSearchParams]);

  const { data, isLoading, isError, refetch } = useLibrary(page, program, urlSearch);
  const firstAvailability = useComparisonAvailability(
    selected[0],
    data?.items.find((item) => item.manuscript_id === selected[0]),
  );
  const secondAvailability = useComparisonAvailability(
    selected[1],
    data?.items.find((item) => item.manuscript_id === selected[1]),
  );
  const selectionStates = selected.length === 0
    ? []
    : selected.length === 1
      ? [firstAvailability]
      : [firstAvailability, secondAvailability];
  const canCompare = selectionStates.length === 2
    && selectionStates.every((selection) => selection.status === "available");
  const selectionHasUnavailable = selectionStates.some(
    (selection) => selection.status === "unavailable",
  );
  const selectionHasError = selectionStates.some((selection) => selection.status === "error");
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const hasFilters = Boolean(program || urlSearch.trim());

  function changeProgram(nextProgram: string | undefined) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (nextProgram) next.set("program", nextProgram);
      else next.delete("program");
      next.set("page", "1");
      return next;
    });
  }

  function changePage(nextPage: number) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("page", String(nextPage));
      return next;
    });
  }

  function toggleSelection(id: number) {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id);
      if (current.length === 2) return current;
      return [...current, id];
    });
  }

  function closeCompareMode() {
    setCompareMode(false);
    setSelected([]);
  }

  async function confirmPurge() {
    if (!purgeTarget) return;
    setPurgeError(null);
    try {
      await purge.mutateAsync(purgeTarget.manuscriptId);
      setPurgeTarget(null);
    } catch (error) {
      setPurgeError(error instanceof ApiError ? error.message : "Stored content could not be removed. Try again.");
    }
  }

  return (
    <div className="signal-route signal-page-flow signal-library" data-compare={compareMode || undefined}>
      <header className="signal-route-header signal-library-header">
        <div>
          <p className="signal-eyebrow">Shared comparison corpus</p>
          <h1 ref={headingRef} tabIndex={-1}>Manuscript Library</h1>
          <p className="signal-route-header__intro">
            Browse the records VERIDICAL can use to surface possible reuse across instructor accounts.
          </p>
        </div>
        <Button
          type="button"
          variant={compareMode ? "quiet" : "brand"}
          onClick={() => compareMode ? closeCompareMode() : setCompareMode(true)}
        >
          {compareMode ? "Exit comparison" : "Compare manuscripts"}
        </Button>
      </header>

      <div className="signal-section-flow signal-library-workspace">
      <aside className="signal-library-privacy">
        <strong>Privacy boundary</strong>
        <p>
          Your account can open its retained source files when they are viewable. Another instructor's record shows only a bounded chapter excerpt, program, and processing date. Student names, the manuscript title, the team name, and the file name are withheld.
          Removing stored content keeps prior reports, decisions, and audit history intact.
        </p>
      </aside>

      <section className="signal-library-controls" aria-label="Library controls">
        <label className="signal-library-filter signal-library-filter--search">
          <span>Search</span>
          <input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Title, author, or group"
          />
        </label>
        <ProgramFilter value={program} onChange={changeProgram} />
        <div className="signal-library-count" aria-live="polite">
          <strong>{data?.total ?? 0}</strong>
          <span>processed records</span>
        </div>
      </section>

      {compareMode && (
        <div className="signal-library-compare-intro">
          <div>
            <strong>Choose manuscripts to compare</strong>
            <span>Select two manuscripts. Comparison is a reading aid and does not assign a new similarity score.</span>
          </div>
          <span role="status" aria-live="polite" aria-atomic="true">
            {selectionStatus(selectionStates)}
          </span>
        </div>
      )}

      {compareMode && selected.length === 2 && (
        <p id="signal-compare-limit" className="sr-only">
          Two manuscripts are already selected. Clear one selection to choose this manuscript.
        </p>
      )}

      <div className="signal-group-flow signal-library-results">
      {isLoading && <div className="signal-library-state" role="status" aria-live="polite" aria-busy="true">Loading library records.</div>}
      {isError && (
        <div className="signal-library-state" role="alert">
          <strong>The library could not be loaded.</strong>
          <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
        </div>
      )}

      {data && data.items.length === 0 && (
        <section className="signal-library-state">
          <p className="signal-eyebrow">No records shown</p>
          <h2>{hasFilters ? "No manuscripts match these filters" : "The library is empty"}</h2>
          <p>
            {hasFilters
              ? "Clear the search and program filter to restore the full corpus."
              : "A record appears after an instructor uploads and checks a manuscript."}
          </p>
          {hasFilters ? (
            <Button type="button" variant="secondary" onClick={() => { setSearchInput(""); setSearchParams({}); }}>Clear filters</Button>
          ) : (
            <ActionLink to="/dashboard" variant="brand">Go to Review Desk</ActionLink>
          )}
        </section>
      )}

      {data && data.items.length > 0 && (
        <ul className="signal-library-grid">
          {data.items.map((item) => (
            <LibraryCard
              key={item.manuscript_id}
              item={item}
              compareMode={compareMode}
              selected={selected.includes(item.manuscript_id)}
              selectionFull={selected.length === 2}
              accessibleSelectionName={accessibleComparisonChoiceName(
                item,
                data.items,
                selected.includes(item.manuscript_id),
              )}
              showProcessedTime={
                data.items.filter((candidate) => identity(candidate).primary === identity(item).primary)
                  .length > 1
              }
              onToggle={() => toggleSelection(item.manuscript_id)}
              onPurge={setPurgeTarget}
            />
          ))}
        </ul>
      )}

      {totalPages > 1 && (
        <nav className="signal-library-pagination" aria-label="Library pages">
          <Button type="button" variant="secondary" disabled={page === 1} onClick={() => changePage(page - 1)}>Previous</Button>
          <span>Page {page} of {totalPages}</span>
          <Button type="button" variant="secondary" disabled={page === totalPages} onClick={() => changePage(page + 1)}>Next</Button>
        </nav>
      )}
      </div>
      </div>

      {compareMode && (
        <aside className="signal-library-selection" aria-label="Comparison selection">
          <div className="signal-library-selection__summary">
            <strong>
              {selected.length === 0
                ? "Select two manuscripts"
                : canCompare
                  ? "Ready to compare"
                  : selectionHasUnavailable
                    ? "Choose another manuscript"
                    : selectionHasError
                      ? "Check availability again"
                      : "Checking selected content"}
            </strong>
            <span>{selected.length} of 2 selected</span>
          </div>
          {selectionStates.length > 0 && (
            <ul className="signal-library-selection__records" aria-label="Selected manuscripts">
              {selectionStates.map((selection) => (
                <li key={selection.manuscriptId} data-status={selection.status}>
                  <strong>{selection.label}</strong>
                  <span>{availabilityLabel(selection.status)}</span>
                  {selection.status === "error" && (
                    <button
                      type="button"
                      className="signal-library-selection__retry"
                      aria-label={`Try availability check again for ${selection.label}`}
                      onClick={selection.retry}
                    >
                      Try again
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="signal-library-selection__actions">
            <Button type="button" variant="quiet" disabled={!selected.length} onClick={() => setSelected([])}>Clear</Button>
            <Button
              type="button"
              variant="brand"
              disabled={!canCompare}
              onClick={() => navigate(`/library/compare?a=${selected[0]}&b=${selected[1]}`)}
            >
              Compare manuscripts
            </Button>
          </div>
        </aside>
      )}

      {purgeTarget && (
        <Dialog
          title={`Remove ${purgeTarget.title}'s stored content?`}
          onClose={purge.isPending ? undefined : () => { setPurgeTarget(null); setPurgeError(null); }}
          actions={
            <>
              <Button type="button" variant="secondary" disabled={purge.isPending} onClick={() => setPurgeTarget(null)}>Cancel</Button>
              <Button type="button" variant="danger" busy={purge.isPending} onClick={confirmPurge}>
                {purge.isPending ? "Removing content" : "Remove stored content"}
              </Button>
            </>
          }
        >
          <div className="signal-library-purge-copy signal-copy-flow">
            <p>The stored file and its future comparison data will be permanently removed.</p>
            <p>Existing check history, readiness reports, instructor decisions, and audit entries remain available.</p>
            <p>This action cannot be undone and never happens automatically.</p>
            {purgeError && <p role="alert">{purgeError}</p>}
          </div>
        </Dialog>
      )}
    </div>
  );
}
