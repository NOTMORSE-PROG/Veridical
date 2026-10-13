// V-077 (D-028), screen 4x: one Library record. A record is a stored
// manuscript and its details, never a verdict: no report link, readiness,
// or score. The owner can edit details, resolve a possible duplicate, and
// remove the record from the Library.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useParams } from "react-router";
import { ApiError, BASE_URL } from "../api/client";
import type { LibraryItemOut } from "../api/types";
import { usePrograms } from "../dashboard/useDashboard";
import { useLibraryRouteFocus } from "./useLibraryRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { LibraryContentPane, type LibraryDocumentState } from "./LibraryContentPane";
import { AuthorFields } from "./SignalLibraryAdd";
import { formatLibraryDate, libraryRecordName, normalizeSchoolYear } from "./libraryFormat";
import {
  useDecideLibraryEntry,
  useLibrary,
  useLibraryDocument,
  useLibraryItem,
  useRemoveLibraryEntry,
  useUpdateLibraryEntry,
} from "./useLibrary";

function headerIntro(item: LibraryItemOut): string {
  if (!item.is_own) return "Another instructor's record. You can read the manuscript; its identifying record fields are hidden.";
  const parts = [item.team_name, item.school_year].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No team name or school year yet";
}

function StatusBanner({
  item,
  onDecide,
  onSkip,
  deciding,
  decideError,
}: {
  item: LibraryItemOut;
  onDecide: (action: "add" | "skip") => void;
  onSkip: () => void;
  deciding: "add" | "skip" | null;
  decideError: string | null;
}) {
  if (item.status === "processing" && item.stalled) {
    return (
      <Alert tone="warning" title="Reading this file stopped">
        <p>
          VERIDICAL did not finish reading this file, so it is not a comparison source. Delete this upload, then add
          the file again.
        </p>
      </Alert>
    );
  }
  if (item.status === "processing") {
    return (
      <Alert tone="info" role="status" title="Reading this file">
        <p>It becomes a comparison source when reading finishes.</p>
      </Alert>
    );
  }
  if (item.status === "needs_decision") {
    const other = item.possible_duplicate_of;
    return (
      <Alert tone="warning" title="Possible duplicate: your decision is needed">
        <p>
          Its text is very similar to your record
          {other ? ` “${other.label}”, added ${formatLibraryDate(other.added_at)}` : ""}. It may be the same
          manuscript in another file, or a revised version. It is not a comparison source until you decide.
        </p>
        <div className="signal-control-cluster">
          {other && (
            <ActionLink to={`/library/compare?a=${item.manuscript_id}&b=${other.manuscript_id}`} variant="quiet">
              Compare the two
            </ActionLink>
          )}
          {other && (
            <ActionLink to={`/library/${other.manuscript_id}`} variant="quiet">Open that record</ActionLink>
          )}
          <Button type="button" variant="secondary" busy={deciding === "add"} disabled={deciding !== null} onClick={() => onDecide("add")}>
            Add anyway
          </Button>
          <Button type="button" variant="secondary" busy={deciding === "skip"} disabled={deciding !== null} onClick={onSkip}>
            Skip this copy
          </Button>
        </div>
        {decideError && <p role="alert">{decideError}</p>}
      </Alert>
    );
  }
  if (item.status === "failed") {
    return (
      <Alert tone="warning" title="Could not read this file">
        <p>{item.failure_detail ?? "VERIDICAL could not read this file."}</p>
        <ActionLink to="/library/add" variant="secondary">Add a text-based copy</ActionLink>
      </Alert>
    );
  }
  return null;
}

function DetailsEditor({
  item,
  onDone,
}: {
  item: LibraryItemOut;
  onDone: (saved: boolean) => void;
}) {
  const titleRef = useRef<HTMLInputElement>(null);
  const update = useUpdateLibraryEntry(item.manuscript_id);
  const programs = usePrograms();
  const ids = { title: useId(), team: useId(), program: useId(), year: useId(), yearError: useId() };
  const [title, setTitle] = useState(item.title ?? "");
  const [teamName, setTeamName] = useState(item.team_name ?? "");
  const [authors, setAuthors] = useState<string[]>(item.authors.length ? item.authors : [""]);
  const [program, setProgram] = useState(item.program ?? "");
  const [schoolYear, setSchoolYear] = useState(item.school_year ?? "");
  const [yearError, setYearError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty = !saved && (
    title !== (item.title ?? "")
    || teamName !== (item.team_name ?? "")
    || program !== (item.program ?? "")
    || schoolYear !== (item.school_year ?? "")
    || authors.filter((a) => a.trim()).join("\n") !== item.authors.join("\n")
  );
  // Unsaved edits are not dropped silently on navigation (UX review P3).
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  async function save() {
    const year = normalizeSchoolYear(schoolYear);
    if (year === null) {
      setYearError("Enter the school year as two consecutive years, for example 2025-2026.");
      return;
    }
    setYearError(null);
    setSaveError(null);
    try {
      await update.mutateAsync({
        title: title.trim() || null,
        team_name: teamName.trim() || null,
        authors: authors.map((a) => a.trim()).filter(Boolean),
        program: program || null,
        school_year: year || null,
      });
      setSaved(true);
      onDone(true);
    } catch (error) {
      setSaveError(error instanceof ApiError ? error.message : "The details could not be saved. Try again.");
    }
  }

  return (
    <form
      className="signal-library-editor"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="signal-field">
        <label htmlFor={ids.title}>Title</label>
        <input id={ids.title} ref={titleRef} maxLength={500} value={title} onChange={(event) => setTitle(event.target.value)} />
      </div>
      <div className="signal-field">
        <label htmlFor={ids.team}>Team name</label>
        <input id={ids.team} maxLength={200} value={teamName} onChange={(event) => setTeamName(event.target.value)} />
      </div>
      <AuthorFields authors={authors} onChange={setAuthors} />
      <label className="signal-library-filter" htmlFor={ids.program}>
        <span>Program</span>
        <select id={ids.program} value={program} onChange={(event) => setProgram(event.target.value)}>
          <option value="">Not set</option>
          {(programs.data ?? []).map((p) => (
            <option key={p.id} value={p.name}>{p.name}</option>
          ))}
        </select>
      </label>
      <div className="signal-field">
        <label htmlFor={ids.year}>School year</label>
        <input
          id={ids.year}
          maxLength={20}
          value={schoolYear}
          aria-invalid={yearError ? true : undefined}
          aria-describedby={yearError ? ids.yearError : undefined}
          onChange={(event) => {
            setSchoolYear(event.target.value);
            setYearError(null);
          }}
        />
        {yearError && <p id={ids.yearError} className="signal-field__error">{yearError}</p>}
      </div>
      {saveError && <p role="alert" className="signal-field__error">{saveError}</p>}
      <div className="signal-form__actions">
        <Button type="submit" variant="primary" busy={update.isPending}>Save details</Button>
        <Button type="button" variant="secondary" disabled={update.isPending} onClick={() => onDone(false)}>
          Cancel
        </Button>
      </div>
      {blocker.state === "blocked" && (
        <Dialog
          title="Leave without saving?"
          onClose={() => blocker.reset()}
          actions={
            <>
              <Button type="button" variant="secondary" onClick={() => blocker.reset()}>Keep editing</Button>
              <Button type="button" variant="danger" onClick={() => blocker.proceed()}>Leave without saving</Button>
            </>
          }
        >
          <p>Your changes to this record's details have not been saved.</p>
        </Dialog>
      )}
    </form>
  );
}

function OwnDetails({ item }: { item: LibraryItemOut }) {
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const restoreEditFocus = useRef(false);
  const [editing, setEditing] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const proposed = item.proposed_fields;

  useLayoutEffect(() => {
    if (editing || !restoreEditFocus.current) return;
    restoreEditFocus.current = false;
    editButtonRef.current?.focus();
  }, [editing]);

  function finishEditing(saved: boolean) {
    restoreEditFocus.current = true;
    setEditing(false);
    setSavedNotice(saved);
  }

  return (
    <div className="signal-library-record__details">
      <div className="signal-library-record__heading">
        <h2>Record details</h2>
        {!editing && item.status !== "removed" && (
          <Button ref={editButtonRef} type="button" variant="secondary" onClick={() => { setSavedNotice(false); setEditing(true); }}>
            Edit details
          </Button>
        )}
      </div>
      <p role="status" className={savedNotice ? "signal-field-hint" : "sr-only"}>{savedNotice ? "Details saved." : ""}</p>
      {editing ? (
        <DetailsEditor item={item} onDone={finishEditing} />
      ) : (
        <>
          {proposed.length > 0 && (
            <p className="signal-field-hint">
              Proposed from the first page of the file, not yet confirmed by you:{" "}
              {proposed.map((field) => (field === "team_name" ? "team name" : field)).join(", ")}.
            </p>
          )}
          <dl className="signal-library-record__metadata">
            {!item.title && <div><dt>Title</dt><dd>Not set</dd></div>}
            <div><dt>Team name</dt><dd>{item.team_name ?? "Not set"}</dd></div>
            <div><dt>Authors</dt><dd>{item.authors.length ? item.authors.join(", ") : "Not listed"}</dd></div>
            <div><dt>Program</dt><dd>{item.program ?? "Not set"}</dd></div>
            <div><dt>School year</dt><dd>{item.school_year ?? "Not set"}</dd></div>
            <div>
              <dt>Added</dt>
              <dd>{item.status === "active" ? formatLibraryDate(item.added_at) : "Not in the Library"}</dd>
            </div>
            <div><dt>File</dt><dd className="signal-library-filename">{item.original_filename ?? "Not recorded"}</dd></div>
            <div>
              <dt>Source</dt>
              <dd>{item.source === "legacy_check_upload" ? "Carried over from an earlier check" : "Uploaded on the Library page"}</dd>
            </div>
          </dl>
          {item.source === "legacy_check_upload" && (
            <p className="signal-field-hint">
              This record came from a manuscript checked before the Library was separate. That check and its report
              are unchanged.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ForeignDetails({ item }: { item: LibraryItemOut }) {
  return (
    <div className="signal-library-record__details">
      <h2>Record details</h2>
      <dl className="signal-library-record__metadata">
        <div><dt>Program</dt><dd>{item.program ?? "Not set"}</dd></div>
        <div><dt>School year</dt><dd>{item.school_year ?? "Not set"}</dd></div>
        <div><dt>Added</dt><dd>{formatLibraryDate(item.added_at)}</dd></div>
      </dl>
      <p className="signal-library-withheld">
        Title, team, authors, file name, and which instructor added it are withheld in record fields.
        You can read the full manuscript, which may include those details. Only its owner can edit or remove it.
      </p>
    </div>
  );
}

export function SignalLibraryDetailPage() {
  const params = useParams<{ manuscriptId: string }>();
  const parsedId = Number(params.manuscriptId);
  const manuscriptId = Number.isInteger(parsedId) && parsedId > 0 ? parsedId : undefined;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const removeButtonRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<"add" | "skip" | null>(null);
  const [confirmingSkip, setConfirmingSkip] = useState(false);
  const [decideError, setDecideError] = useState<string | null>(null);
  const remove = useRemoveLibraryEntry();
  const decide = useDecideLibraryEntry();
  const { data: item, isPending, isError, error, refetch } = useLibraryItem(manuscriptId);
  // Compare needs a second active record somewhere in the Library.
  const collection = useLibrary(1, undefined, "");
  const canCompare = item?.status === "active" && (collection.data?.total ?? 0) >= 2;
  const documentQuery = useLibraryDocument(manuscriptId, Boolean(item && item.status !== "removed"));
  const documentState: LibraryDocumentState = {
    viewer: documentQuery.data,
    isPending: documentQuery.isPending,
    isError: documentQuery.isError,
    removedAt: null,
    onRetry: () => void documentQuery.refetch(),
  };
  const name = item ? libraryRecordName(item) : null;
  const notFound = isError && error instanceof ApiError && error.status === 404;

  useLibraryRouteFocus(name ? `${name} - Manuscript Library - VERIDICAL` : "Manuscript Library - VERIDICAL", headingRef);

  async function confirmRemove() {
    if (!manuscriptId || !name) return;
    setRemoveError(null);
    try {
      await remove.mutateAsync(manuscriptId);
      navigate(
        "/library",
        item?.status === "active"
          ? { state: { removedTitle: name } }
          : { state: { deletedTitle: item?.original_filename ?? name } },
      );
    } catch (removeFailure) {
      setRemoveError(
        removeFailure instanceof ApiError ? removeFailure.message : "The record could not be removed. Try again.",
      );
    }
  }

  async function decideEntry(action: "add" | "skip") {
    if (!manuscriptId || !name) return;
    setDeciding(action);
    setDecideError(null);
    try {
      await decide.mutateAsync({ manuscriptId, action });
      if (action === "skip") navigate("/library", { state: { skippedTitle: item?.original_filename ?? name } });
      else await refetch();
    } catch (decisionFailure) {
      setDecideError(
        decisionFailure instanceof ApiError ? decisionFailure.message : "The decision could not be saved. Try again.",
      );
    } finally {
      setDeciding(null);
    }
  }

  const isSubmission = item?.source === "library_submission";
  // Only an active record is removed from the Library; a failed or stalled
  // upload is deleted. A live upload cannot be removed mid-read, and a
  // possible duplicate is resolved with Add anyway or Skip this copy.
  const removeLabel = item?.status === "active" ? "Remove from Library" : "Delete upload";
  const canRemove = Boolean(
    item?.is_own
      && (item.status === "active" || item.status === "failed" || (item.status === "processing" && item.stalled)),
  );

  return (
    <div className="signal-route signal-page-flow signal-library-detail">
      <header className="signal-route-header signal-library-detail__header">
        <div>
          <nav className="signal-breadcrumb" aria-label="Breadcrumb">
            <Link to="/library">Manuscript Library</Link>
            <span aria-hidden="true">/</span>
            <span>Record</span>
          </nav>
          <p className="signal-eyebrow">{!item || item.status === "active" ? "Library record" : "Your upload, not in the Library"}</p>
          <h1 ref={headingRef} tabIndex={-1}>{name ?? "Library record"}</h1>
          {item && <p className="signal-route-header__intro">{headerIntro(item)}</p>}
        </div>
        {item && (
          <div className="signal-control-cluster">
            {documentQuery.data?.available && (
              <a
                className="signal-button signal-button--secondary"
                href={BASE_URL + '/library/' + manuscriptId + '/document/original'}
              >
                Download original
              </a>
            )}
            {canCompare && (
              <ActionLink to={`/library?compareFrom=${item.manuscript_id}`} variant="secondary">
                Compare with another record
              </ActionLink>
            )}
            {canRemove && (
              <Button ref={removeButtonRef} type="button" variant="danger" onClick={() => setConfirmingRemove(true)}>
                {removeLabel}
              </Button>
            )}
          </div>
        )}
      </header>

      {!manuscriptId && (
        <section className="signal-library-state" role="alert">
          <h2>This record address is not valid</h2>
          <p>Return to the Library and open a listed record.</p>
          <ActionLink to="/library" variant="brand">Return to Library</ActionLink>
        </section>
      )}
      {manuscriptId && isPending && (
        <div className="signal-library-state" role="status" aria-live="polite" aria-busy="true">Loading Library record.</div>
      )}
      {manuscriptId && notFound && (
        <section className="signal-library-state" role="alert">
          <h2>This Library record is not available</h2>
          <p>It may have been removed from the Library.</p>
          <ActionLink to="/library" variant="secondary">Return to Library</ActionLink>
        </section>
      )}
      {manuscriptId && isError && !notFound && (
        <section className="signal-library-state" role="alert">
          <h2>This record could not be loaded</h2>
          <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
        </section>
      )}

      {manuscriptId && item && (
        <>
          {item.is_own && (
            <StatusBanner
              item={item}
              onDecide={(action) => void decideEntry(action)}
              onSkip={() => setConfirmingSkip(true)}
              deciding={deciding}
              decideError={decideError}
            />
          )}
          <section className="signal-library-workbench">
            <div className="signal-library-workbench__grid">
              <div
                role="region"
                aria-label="Manuscript"
                className="signal-library-source"
              >
                <LibraryContentPane
                  manuscriptId={manuscriptId}
                  documentState={documentState}
                />
              </div>
              <aside
                role="region"
                aria-label="Record details"
                className="signal-library-record"
              >
                {item.is_own ? <OwnDetails item={item} /> : <ForeignDetails item={item} />}
              </aside>
            </div>
          </section>
        </>
      )}

      {confirmingSkip && item && (
        <Dialog
          title="Skip this copy?"
          onClose={() => setConfirmingSkip(false)}
          actions={
            <>
              <Button type="button" variant="secondary" onClick={() => setConfirmingSkip(false)}>Keep deciding</Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  setConfirmingSkip(false);
                  void decideEntry("skip");
                }}
              >
                Skip and delete this copy
              </Button>
            </>
          }
        >
          <p>
            The uploaded file “{item.original_filename ?? name}” will not be added and will not be kept.
            {item.possible_duplicate_of && ` Your existing record “${item.possible_duplicate_of.label}” is not affected.`}
          </p>
        </Dialog>
      )}

      {confirmingRemove && item && name && (
        <Dialog
          title={item.status === "active" ? `Remove “${name}” from the Library?` : "Delete this upload?"}
          onClose={remove.isPending ? undefined : () => {
            setConfirmingRemove(false);
            setRemoveError(null);
            requestAnimationFrame(() => removeButtonRef.current?.focus());
          }}
          actions={
            <>
              <Button
                type="button"
                variant="secondary"
                disabled={remove.isPending}
                onClick={() => {
                  setConfirmingRemove(false);
                  requestAnimationFrame(() => removeButtonRef.current?.focus());
                }}
              >
                Cancel
              </Button>
              <Button type="button" variant="danger" busy={remove.isPending} onClick={() => void confirmRemove()}>
                {remove.isPending
                  ? "Removing"
                  : item.status !== "active"
                    ? "Delete upload"
                    : isSubmission
                      ? "Delete file and remove"
                      : "Remove from Library"}
              </Button>
            </>
          }
        >
          <div className="signal-copy-flow">
            {item.status !== "active" ? (
              <p>“{name}” is not in the Library. Deleting removes the stored file.</p>
            ) : isSubmission ? (
              <p>
                The stored file and its comparison data will be deleted, and future originality checks will not compare
                against it. Reports from checks that already ran keep the findings they recorded. This cannot be undone.
              </p>
            ) : (
              <p>
                Future originality checks will stop comparing against this manuscript, and its comparison data will be
                deleted. Its own check report, instructor decision, and audit history are not affected. To add it back
                later, add the file from Add to Library.
              </p>
            )}
            {removeError && <p role="alert">{removeError}</p>}
          </div>
        </Dialog>
      )}
    </div>
  );
}
