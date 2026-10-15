// V-077 (D-028), screen 4z: the Library as a plain collection of
// manuscripts and the only originality corpus. No readiness band, score,
// flag, report link, or similarity judgment appears anywhere here.
import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { LibraryItemOut } from "../api/types";
import { LIBRARY_SEARCH_DEBOUNCE_MS, UNSET_PROGRAM_FILTER } from "../config/ui";
import { usePrograms } from "../dashboard/useDashboard";
import { useLibraryRouteFocus } from "./useLibraryRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { LibraryEntryStatus } from "./LibraryEntryStatus";
import { formatLibraryDate, libraryRecordName } from "./libraryFormat";
import {
  useLibrary,
  useLibraryDocument,
  useLibraryItem,
  useLibraryParagraphs,
  useLibraryPending,
} from "./useLibrary";

type ComparisonAvailabilityStatus = "checking" | "available" | "unavailable" | "error";

interface ComparisonAvailability {
  manuscriptId: number | undefined;
  label: string;
  status: ComparisonAvailabilityStatus;
  retry: () => void;
}

function selectionStatus(selections: ComparisonAvailability[]): string {
  if (selections.length === 0) return "No records selected. Select two records.";
  const hasUnavailable = selections.some((selection) => selection.status === "unavailable");
  const hasError = selections.some((selection) => selection.status === "error");
  const isChecking = selections.some((selection) => selection.status === "checking");
  if (selections.length === 1) {
    if (hasUnavailable) return "The selected record has no viewable content. Clear it and choose another.";
    if (hasError) return "The selected record's availability could not be checked. Try again before choosing another.";
    if (isChecking) return "One record selected. Checking its content.";
    return "One record selected and available. Select one more record.";
  }
  if (hasUnavailable) return "At least one selected record has no viewable content. Clear it and choose another.";
  if (hasError) return "One or more selected records could not be checked. Try again before comparing.";
  if (isChecking) return "Two records selected. Checking their content.";
  return "Two records selected and available. You can compare them now.";
}

function availabilityLabel(status: ComparisonAvailabilityStatus): string {
  if (status === "checking") return "Checking content";
  if (status === "available") return "Available to compare";
  if (status === "unavailable") return "No viewable content";
  return "Could not check availability";
}

function useComparisonAvailability(
  manuscriptId: number | undefined,
  listedItem: LibraryItemOut | undefined,
): ComparisonAvailability {
  // A selected item normally comes from the already-loaded page. Only a
  // compareFrom deep link may require one bounded metadata request.
  const itemQuery = useLibraryItem(manuscriptId, Boolean(manuscriptId && !listedItem));
  const item = listedItem ?? itemQuery.data;
  const canReadContent = item?.status === "active";
  const documentQuery = useLibraryDocument(manuscriptId, canReadContent);
  const paragraphsQuery = useLibraryParagraphs(
    manuscriptId,
    canReadContent
      && documentQuery.data?.available === true
      && documentQuery.data.source_format === "docx",
  );

  let status: ComparisonAvailabilityStatus = "checking";
  if (itemQuery.isError && !listedItem) {
    status = "error";
  } else if (item) {
    if (item.status !== "active") {
      status = "unavailable";
    } else {
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
    }
  }

  const label = item
    ? [libraryRecordName(item), formatLibraryDate(item.added_at)].join(" · ")
    : manuscriptId
      ? `Library record #${manuscriptId}`
      : "Library record";

  return {
    manuscriptId,
    label,
    status,
    retry: () => {
      if (!item) void itemQuery.refetch();
      else if (
        documentQuery.data?.available === true
        && documentQuery.data.source_format === "docx"
      ) void paragraphsQuery.refetch();
      else void documentQuery.refetch();
    },
  };
}

// BUG-187: repeated checkboxes need a unique, record-specific accessible
// name; the visible, privacy-safe metadata disambiguates.
function accessibleComparisonChoiceName(
  item: LibraryItemOut,
  items: LibraryItemOut[],
  selected: boolean,
): string {
  const name = libraryRecordName(item);
  const sameName = items.filter((candidate) => libraryRecordName(candidate) === name);
  let disambiguator = "";
  if (sameName.length > 1) {
    const added = formatLibraryDate(item.added_at);
    const sameDate = sameName.filter((candidate) => formatLibraryDate(candidate.added_at) === added);
    disambiguator = sameDate.length === 1 ? `, added ${added}` : `, record #${item.manuscript_id}`;
  }
  const action = selected ? "Remove" : "Select";
  return `${action} “${name}”${disambiguator} ${selected ? "from" : "for"} comparison`;
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

function pendingDetail(item: LibraryItemOut): string {
  if (item.status === "processing") return "Still reading this file.";
  if (item.status === "needs_decision") {
    const other = item.possible_duplicate_of?.label;
    return other
      ? `Its text is very similar to your record “${other}”.`
      : "Its text is very similar to one of your records.";
  }
  const detail = item.failure_detail ?? "VERIDICAL could not read this file.";
  const firstSentence = detail.split(/(?<=\.)\s/)[0];
  return firstSentence;
}

function PendingUploads({ items }: { items: LibraryItemOut[] }) {
  return (
    <section className="signal-library-pending" aria-labelledby="signal-library-pending-heading">
      <h2 id="signal-library-pending-heading">Your uploads not in the Library</h2>
      <p>
        Still being read, waiting for your decision, or could not be read. None of these are used in originality
        checks.
      </p>
      <ul>
        {items.map((item) => (
          <li key={item.manuscript_id} className="signal-library-pending__row">
            <div className="signal-library-pending__identity">
              <strong>{item.original_filename ?? libraryRecordName(item)}</strong>
              <LibraryEntryStatus status={item.status} />
              <span>{pendingDetail(item)}</span>
            </div>
            <ActionLink
              to={`/library/${item.manuscript_id}`}
              variant={item.status === "needs_decision" ? "secondary" : "quiet"}
              aria-label={`${item.status === "needs_decision" ? "Review possible duplicate" : "Open record"} for ${item.original_filename ?? libraryRecordName(item)}`}
            >
              {item.status === "needs_decision" ? "Review possible duplicate" : "Open record"}
            </ActionLink>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LibraryCard({
  item,
  compareMode,
  selected,
  selectionFull,
  accessibleSelectionName,
  onToggle,
}: {
  item: LibraryItemOut;
  compareMode: boolean;
  selected: boolean;
  selectionFull: boolean;
  accessibleSelectionName: string;
  onToggle: () => void;
}) {
  const disabled = !selected && selectionFull;
  const name = libraryRecordName(item);
  const authors = item.is_own ? item.authors : [];
  const otherAuthors = authors.length - 1;
  const authorSummary = authors.length
    ? authors[0] + (otherAuthors > 0 ? ` + ${otherAuthors} ${otherAuthors === 1 ? "other" : "others"}` : "")
    : "";
  const contextId = 'library-context-' + item.manuscript_id;
  const titleId = 'library-title-' + item.manuscript_id;
  const programSummary = [item.program, item.school_year ? `School year ${item.school_year}` : null]
    .filter(Boolean).join(" \u00b7 ");
  return (
    <li>
      <article className="signal-library-card" aria-labelledby={titleId} data-selected={selected || undefined}>
        <div className="signal-library-card__body">
          <p id={contextId} className="signal-library-card__context">
            {item.is_own ? <>Your record <span aria-hidden="true">&middot;</span> <span>#{item.manuscript_id}</span></> : "Shared record"}
          </p>
          <div className="signal-library-card__identity">
            <h2 id={titleId}>{name}</h2>
          </div>
          <p className="signal-library-card__summary" aria-hidden={!authorSummary || undefined}>{authorSummary}</p>
          <p className="signal-library-card__summary" aria-hidden={!programSummary || undefined}>{programSummary}</p>
          <footer className="signal-library-card__footer">
            <p className="signal-library-card__date">
              {item.added_at ? <>Added <time dateTime={item.added_at}>{formatLibraryDate(item.added_at)}</time></> : "Added date unavailable"}
            </p>
            <div className="signal-library-card__footer-actions">
              <ActionLink
                to={'/library/' + item.manuscript_id}
                variant="quiet"
                aria-label={'View manuscript: ' + name}
                aria-describedby={contextId}
              >
                View manuscript
              </ActionLink>
              {compareMode && (
                <label className="signal-library-select" data-disabled={disabled || undefined}>
                  <input
                    type="checkbox"
                    checked={selected}
                    aria-label={accessibleSelectionName}
                    aria-disabled={disabled || undefined}
                    aria-describedby={disabled ? "signal-compare-limit" : undefined}
                    onChange={() => { if (!disabled) onToggle(); }}
                  />
                  <span>{selected ? "Selected" : "Select"}</span>
                </label>
              )}
            </div>
          </footer>
        </div>
      </article>
    </li>
  );
}

interface LibraryLocationState {
  removedTitle?: string;
  skippedTitle?: string;
  deletedTitle?: string;
  comparisonSelection?: number[];
}

export function SignalLibraryPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useLibraryRouteFocus("Manuscript Library - VERIDICAL", headingRef);
  const navigate = useNavigate();
  const location = useLocation();
  // Captured once on arrival: later URL updates (search, page) replace the
  // history entry and would otherwise drop the notice (ux-critic P2-4).
  const [arrivalNotice] = useState(() => location.state as LibraryLocationState | null);
  const removedTitle = arrivalNotice?.removedTitle;
  const skippedTitle = arrivalNotice?.skippedTitle;
  const deletedTitle = arrivalNotice?.deletedTitle;
  const [searchParams, setSearchParams] = useSearchParams();
  const parsedPage = Number(searchParams.get("page") ?? "1");
  const page = Number.isInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;
  const program = searchParams.get("program") ?? undefined;
  const urlSearch = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(urlSearch);
  const compareFrom = Number(searchParams.get("compareFrom"));
  const initialSelection = [...new Set(
    arrivalNotice?.comparisonSelection
      ?? (Number.isInteger(compareFrom) && compareFrom > 0 ? [compareFrom] : []),
  )].filter((id) => Number.isInteger(id) && id > 0).slice(0, 2);
  const [compareMode, setCompareMode] = useState(initialSelection.length > 0);
  const [selected, setSelected] = useState<number[]>(initialSelection);

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
      const query = searchInput.trim();
      if (query === urlSearch) return;
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        if (query) next.set("q", query);
        else next.delete("q");
        next.set("page", "1");
        return next;
      }, { replace: true });
    }, LIBRARY_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [searchInput, urlSearch, setSearchParams]);

  const { data, isLoading, isError, refetch } = useLibrary(page, program, urlSearch);
  // The result count belongs to this filter; comparison availability belongs
  // to the whole Library. The common unfiltered query shares the same cache.
  const collection = useLibrary(1, undefined, "");
  const pending = useLibraryPending();
  const pendingItems = pending.data?.items ?? [];
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
  const selectionHasUnavailable = selectionStates.some((selection) => selection.status === "unavailable");
  const selectionHasError = selectionStates.some((selection) => selection.status === "error");
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const hasSearch = Boolean(urlSearch.trim());
  const hasFilters = Boolean(program || hasSearch);
  const total = data?.total ?? 0;
  const corpusTotal = collection.data?.total ?? (!hasFilters ? data?.total : undefined);
  const libraryIsEmpty = Boolean(data && !hasFilters && total === 0);

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

  function clearFilters() {
    setSearchInput("");
    setSearchParams({});
  }

  let emptyHeading = "No records match your search";
  if (program && hasSearch) emptyHeading = "No records match your search and program";
  else if (program) emptyHeading = "No records match this program";

  return (
    <div className="signal-route signal-page-flow signal-library" data-compare={compareMode || undefined}>
      <header className="signal-route-header signal-library-header">
        <div>
          <p className="signal-eyebrow">Originality comparison source</p>
          <h1 ref={headingRef} tabIndex={-1}>Manuscript Library</h1>
          <p className="signal-route-header__intro">
            Store manuscripts for originality comparisons. Checking a manuscript does not add it here.
          </p>
        </div>
        <ActionLink to="/library/add" variant="brand">Add to Library</ActionLink>
      </header>

      <div className="signal-section-flow signal-library-workspace">
        {removedTitle && (
          <Alert tone="success" role="status" title={`“${removedTitle}” was removed from the Library.`} />
        )}
        {skippedTitle && (
          <Alert
            tone="info"
            role="status"
            title={`The uploaded copy “${skippedTitle}” was skipped and not kept. Your existing record is unchanged.`}
          />
        )}
        {deletedTitle && (
          <Alert tone="info" role="status" title={`The upload “${deletedTitle}” was deleted.`} />
        )}

        <aside className="signal-library-privacy">
          <strong>Who can read Library manuscripts</strong>
          <p>
            Signed-in instructors can read the full manuscript for every active record. Identifying fields
            on shared records stay hidden, but the manuscript itself may contain those details.
            Only the instructor who added a record can edit or remove it.
          </p>
        </aside>

        {pendingItems.length > 0 && <PendingUploads items={pendingItems} />}

        {!libraryIsEmpty && (
          <section className="signal-library-controls" aria-label="Search and filter Library">
            <div className="signal-library-controls__fields">
              <label className="signal-library-filter signal-library-filter--search">
                <span>Search Library</span>
                <input
                  type="search"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="Title, team, author, file name, or #record"
                  aria-describedby="signal-library-search-hint"
                />
              </label>
              <p id="signal-library-search-hint" className="signal-field-hint">
                Search your titles, teams, authors and file names. Use a record number such as #20 to find any Library record.
              </p>
              <ProgramFilter value={program} onChange={changeProgram} />
            </div>
          </section>
        )}

        <div className="signal-library-results-header">
          <h2 aria-live="polite" aria-atomic="true">
            {data ? (
              <><strong>{total}</strong> {hasFilters ? "matching" : "Library"} {total === 1 ? "manuscript" : "manuscripts"}</>
            ) : "Library manuscripts"}
          </h2>
          {compareMode || (corpusTotal !== undefined && corpusTotal >= 2) ? (
            <Button
              type="button"
              variant={compareMode ? "quiet" : "secondary"}
              onClick={() => compareMode ? closeCompareMode() : setCompareMode(true)}
            >
              {compareMode ? "Exit comparison" : "Compare two records"}
            </Button>
          ) : (
            corpusTotal === 1 && <p className="signal-field-hint">Comparison needs at least two Library records.</p>
          )}
        </div>

        {compareMode && (
          <div className="signal-library-compare-intro">
            <div>
              <strong>Choose two records to compare</strong>
              <span>Comparison is a reading aid and does not assign a new similarity score.</span>
            </div>
            <span role="status" aria-live="polite" aria-atomic="true">
              {selectionStatus(selectionStates)}
            </span>
          </div>
        )}

      {compareMode && (
        <aside className="signal-library-selection" aria-label="Comparison selection">
          <div className="signal-library-selection__summary">
            <strong>
              {selected.length === 0
                ? "Select two records"
                : canCompare
                  ? "Ready to compare"
                  : selectionHasUnavailable
                    ? "Choose another record"
                    : selectionHasError
                      ? "Check availability again"
                      : "Checking the selected records can be shown"}
            </strong>
            <span>{selected.length} of 2 selected</span>
          </div>
          {selectionStates.length > 0 && (
            <ul className="signal-library-selection__records" aria-label="Selected records">
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
            <Button type="button" variant="quiet" disabled={!selected.length} onClick={() => setSelected([])}>
              Clear
            </Button>
            <Button
              type="button"
              variant="brand"
              disabled={!canCompare}
              onClick={() => navigate(`/library/compare?a=${selected[0]}&b=${selected[1]}`, {
                state: { collectionSearch: searchParams.toString() },
              })}
            >
              Compare records
            </Button>
          </div>
        </aside>
      )}

        {compareMode && selected.length === 2 && (
          <p id="signal-compare-limit" className="sr-only">
            Two records are already selected. Clear one selection to choose this record.
          </p>
        )}

        <div className="signal-group-flow signal-library-results">
          {isLoading && (
            <div className="signal-library-state" role="status" aria-live="polite" aria-busy="true">
              Loading library records.
            </div>
          )}
          {isError && (
            <div className="signal-library-state" role="alert">
              <strong>The library could not be loaded.</strong>
              <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
            </div>
          )}

          {libraryIsEmpty && (
            <section className="signal-library-state">
              {pendingItems.length > 0 ? (
                <>
                  <h2>No manuscripts are in the Library yet</h2>
                  <p>Your uploads above join it once each one is resolved.</p>
                </>
              ) : (
                <>
                  <h2>The Library has no manuscripts yet</h2>
                  <p>
                    Originality checks compare only against Library manuscripts, so right now they have
                    nothing to compare against. Add finished manuscripts, such as defended capstones from
                    past terms.
                  </p>
                  <ActionLink to="/library/add" variant="secondary">Add to Library</ActionLink>
                </>
              )}
            </section>
          )}

          {data && hasFilters && data.items.length === 0 && (
            <section className="signal-library-state">
              <h2>{emptyHeading}</h2>
              {hasSearch && <p>Search covers your own records only.</p>}
              <Button type="button" variant="secondary" onClick={clearFilters}>
                Clear search and filter
              </Button>
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
                  onToggle={() => toggleSelection(item.manuscript_id)}
                />
              ))}
            </ul>
          )}

          {totalPages > 1 && (
            <nav className="signal-library-pagination" aria-label="Library pages">
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
      </div>



    </div>
  );
}
