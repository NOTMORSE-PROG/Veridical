// V-077 (D-028), new screen 4aa: add manuscripts to the Library. Adding
// stores a file as a comparison source and nothing else: no rubric, no
// grade, no report. Files are sent one at a time (one extraction in memory
// on the free-tier worker), and each outcome is announced as it lands.
import { useEffect, useId, useRef, useState } from "react";
import { Link, useBlocker } from "react-router";
import { ApiError } from "../api/client";
import type { LibraryDuplicateRefOut, LibraryItemOut, LibrarySubmissionOut } from "../api/types";
import { usePrograms } from "../dashboard/useDashboard";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { LibraryEntryStatus } from "./LibraryEntryStatus";
import { formatLibraryDate, normalizeSchoolYear } from "./libraryFormat";
import {
  submitLibraryEntry,
  useDecideLibraryEntry,
  useLibraryInvalidation,
  useRemoveLibraryEntry,
} from "./useLibrary";

const ACCEPT =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];

type RowState =
  | { kind: "queued" }
  | { kind: "sending" }
  | { kind: "added"; item: LibraryItemOut }
  | { kind: "processing"; item: LibraryItemOut }
  | { kind: "exact"; duplicate: LibraryDuplicateRefOut | null; message: string }
  | {
      kind: "possible";
      item: LibraryItemOut;
      duplicate: LibraryDuplicateRefOut | null;
      deciding?: "add" | "skip";
      error?: string;
    }
  | { kind: "added_anyway"; manuscriptId: number }
  | { kind: "skipped" }
  | { kind: "failed"; message: string; item: LibraryItemOut | null }
  | { kind: "deleted" }
  | { kind: "not_sent" }
  | { kind: "rejected"; message: string };

interface Row {
  key: string;
  file: File;
  state: RowState;
}

function fileKey(file: File): string {
  return `${file.name}::${file.size}::${file.lastModified}`;
}

function isAcceptedFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function formatSize(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function rowStatusLabel(state: RowState): string {
  switch (state.kind) {
    case "queued":
      return "Waiting";
    case "sending":
      return "Adding";
    case "added":
    case "added_anyway":
      return "Added";
    case "processing":
      return "Reading file";
    case "exact":
      return "Not added: you already uploaded this file";
    case "possible":
      return "Possible duplicate";
    case "skipped":
      return "Skipped";
    case "failed":
      return "Could not read";
    case "deleted":
      return "Upload deleted";
    case "not_sent":
      return "Not sent";
    case "rejected":
      return "Could not add";
  }
}

function isSettled(state: RowState): boolean {
  return state.kind !== "queued" && state.kind !== "sending";
}

function outcomeFromSubmission(result: LibrarySubmissionOut): RowState {
  if (result.outcome === "exact_duplicate") {
    return { kind: "exact", duplicate: result.duplicate_of, message: result.message };
  }
  if (result.outcome === "possible_duplicate" && result.item) {
    return { kind: "possible", item: result.item, duplicate: result.duplicate_of };
  }
  if (result.outcome === "failed") return { kind: "failed", message: result.message, item: result.item };
  if (result.item?.status === "processing") return { kind: "processing", item: result.item };
  if (result.item) return { kind: "added", item: result.item };
  return { kind: "rejected", message: result.message };
}

function RowDetail({ row }: { row: Row }) {
  const { state } = row;
  if (state.kind === "sending") return <p>Reading the text. Longer files can take up to a minute.</p>;
  if (state.kind === "added") {
    const fromTitlePage = state.item.proposed_fields.includes("title");
    return (
      <p>
        Added as “{state.item.label}”.
        {fromTitlePage && " This title was proposed from the first page; check it on the record."}
      </p>
    );
  }
  if (state.kind === "processing") return <p>It joins the Library when reading finishes.</p>;
  if (state.kind === "exact") {
    return (
      <p>
        {state.message}
        {state.duplicate && ` Your record: “${state.duplicate.label}”.`}
      </p>
    );
  }
  if (state.kind === "possible") {
    return (
      <p>
        Its text is very similar to your record
        {state.duplicate ? ` “${state.duplicate.label}”${state.duplicate.added_at ? `, added ${formatLibraryDate(state.duplicate.added_at)}` : ""},` : ""}{" "}
        but the file is different. It may be the same manuscript in another file, or a revised version. It has not
        been added.
      </p>
    );
  }
  if (state.kind === "added_anyway") return <p>Added anyway. It is now a comparison source.</p>;
  if (state.kind === "skipped") return <p>Not added. This copy was not kept.</p>;
  if (state.kind === "failed") return <p>{state.message}</p>;
  if (state.kind === "deleted") return <p>The unreadable upload was deleted.</p>;
  if (state.kind === "not_sent") return <p>This file was not sent. Nothing was added.</p>;
  if (state.kind === "rejected") return <p>{state.message}</p>;
  return null;
}

export function SignalLibraryAddPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const batchHeadingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Add manuscripts - VERIDICAL", headingRef);
  const invalidateLibrary = useLibraryInvalidation();
  const decide = useDecideLibraryEntry();
  const remove = useRemoveLibraryEntry();
  const programs = usePrograms();

  const fileInputId = useId();
  const filesHintId = useId();
  const filesErrorId = useId();
  const yearId = useId();
  const yearHintId = useId();
  const yearErrorId = useId();
  const programId = useId();
  const titleId = useId();
  const teamId = useId();

  const [files, setFiles] = useState<File[]>([]);
  const [typeErrors, setTypeErrors] = useState<string[]>([]);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [program, setProgram] = useState("");
  const [schoolYear, setSchoolYear] = useState("");
  const [yearError, setYearError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [teamName, setTeamName] = useState("");
  const [authors, setAuthors] = useState<string[]>([""]);

  const [phase, setPhase] = useState<"form" | "batch">("form");
  const [rows, setRows] = useState<Row[]>([]);
  const [running, setRunning] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Row | null>(null);
  const [skipTarget, setSkipTarget] = useState<Row | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const rowsRef = useRef<Row[]>([]);
  const stopRequested = useRef(false);
  const submissionRef = useRef<{ program?: string; schoolYear?: string; title?: string; teamName?: string; authors?: string[] }>({});

  const singleFile = files.length === 1;
  const detailsFilled = Boolean(title.trim() || teamName.trim() || authors.some((a) => a.trim()));
  const formDirty = phase === "form" && (files.length > 0 || detailsFilled || Boolean(schoolYear.trim()));
  const unsent = rows.filter((row) => row.state.kind === "queued").length;
  const batchUnsafe = phase === "batch" && running && unsent > 0;

  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    (formDirty || batchUnsafe) && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!formDirty && !batchUnsafe) return;
    function beforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [formDirty, batchUnsafe]);

  function updateRow(key: string, state: RowState) {
    rowsRef.current = rowsRef.current.map((row) => (row.key === key ? { ...row, state } : row));
    setRows(rowsRef.current);
  }

  function chooseFiles(list: FileList | null) {
    if (!list) return;
    const rejected: string[] = [];
    const next = [...files];
    const known = new Set(next.map(fileKey));
    for (const file of Array.from(list)) {
      if (!isAcceptedFile(file)) {
        rejected.push(file.name);
        continue;
      }
      if (!known.has(fileKey(file))) {
        known.add(fileKey(file));
        next.push(file);
      }
    }
    setFiles(next);
    setTypeErrors(rejected);
    setFilesError(null);
  }

  function removeFile(key: string) {
    setFiles((current) => current.filter((file) => fileKey(file) !== key));
  }

  function normalizedYear(): string | null {
    return normalizeSchoolYear(schoolYear);
  }

  async function sendOne(row: Row): Promise<"ok" | "stop"> {
    updateRow(row.key, { kind: "sending" });
    const shared = submissionRef.current;
    try {
      const result = await submitLibraryEntry({ file: row.file, ...shared });
      const state = outcomeFromSubmission(result);
      updateRow(row.key, state);
      const done = rowsRef.current.filter((r) => isSettled(r.state)).length;
      setAnnouncement(`${row.file.name}: ${rowStatusLabel(state)}. ${done} of ${rowsRef.current.length} done.`);
      return "ok";
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        updateRow(row.key, { kind: "not_sent" });
        return "stop";
      }
      if (error instanceof ApiError && error.status < 500) {
        updateRow(row.key, { kind: "rejected", message: error.message });
      } else {
        updateRow(row.key, { kind: "not_sent" });
      }
      return "ok";
    }
  }

  async function runQueue() {
    setRunning(true);
    setRateLimited(false);
    for (const row of rowsRef.current) {
      if (stopRequested.current) break;
      const current = rowsRef.current.find((r) => r.key === row.key);
      if (!current || current.state.kind !== "queued") continue;
      const result = await sendOne(current);
      if (result === "stop") {
        setRateLimited(true);
        for (const remaining of rowsRef.current) {
          if (remaining.state.kind === "queued") updateRow(remaining.key, { kind: "not_sent" });
        }
        break;
      }
    }
    setRunning(false);
    invalidateLibrary();
    const settled = rowsRef.current;
    const added = settled.filter((r) => ["added", "processing", "added_anyway"].includes(r.state.kind)).length;
    const waiting = settled.filter((r) => r.state.kind === "possible").length;
    const unreadable = settled.filter((r) => r.state.kind === "failed").length;
    const parts = [
      added ? `${added} added` : "",
      waiting ? `${plural(waiting, "possible duplicate", "possible duplicates")} waiting for your decision` : "",
      unreadable ? `${unreadable} could not be read` : "",
    ].filter(Boolean);
    setAnnouncement(parts.length ? `Finished. ${parts.join(", ")}.` : "Finished.");
  }

  function submit() {
    if (files.length === 0) {
      setFilesError("Choose at least one PDF or DOCX file.");
      requestAnimationFrame(() => document.getElementById(fileInputId)?.focus());
      return;
    }
    const year = normalizedYear();
    if (year === null) {
      setYearError("Enter the school year as two consecutive years, for example 2025-2026.");
      requestAnimationFrame(() => document.getElementById(yearId)?.focus());
      return;
    }
    setYearError(null);
    submissionRef.current = {
      program: program || undefined,
      schoolYear: year || undefined,
      ...(singleFile
        ? {
            title: title.trim() || undefined,
            teamName: teamName.trim() || undefined,
            authors: authors.map((a) => a.trim()).filter(Boolean),
          }
        : {}),
    };
    stopRequested.current = false;
    rowsRef.current = files.map((file) => ({ key: fileKey(file), file, state: { kind: "queued" } }));
    setRows(rowsRef.current);
    setPhase("batch");
    requestAnimationFrame(() => batchHeadingRef.current?.focus());
    void runQueue();
  }

  function retryUnsent() {
    for (const row of rowsRef.current) {
      if (row.state.kind === "not_sent") updateRow(row.key, { kind: "queued" });
    }
    stopRequested.current = false;
    void runQueue();
  }

  async function decideRow(row: Row, action: "add" | "skip") {
    if (row.state.kind !== "possible") return;
    const item = row.state.item;
    updateRow(row.key, { ...row.state, deciding: action, error: undefined });
    try {
      await decide.mutateAsync({ manuscriptId: item.manuscript_id, action });
      updateRow(
        row.key,
        action === "add" ? { kind: "added_anyway", manuscriptId: item.manuscript_id } : { kind: "skipped" },
      );
      setAnnouncement(`${row.file.name}: ${action === "add" ? "added anyway" : "skipped, this copy was not kept"}.`);
      requestAnimationFrame(() => document.getElementById(`library-add-status-${row.key}`)?.focus());
    } catch (error) {
      const latest = rowsRef.current.find((r) => r.key === row.key);
      if (latest?.state.kind === "possible") {
        updateRow(row.key, {
          ...latest.state,
          deciding: undefined,
          error: error instanceof ApiError ? error.message : "The decision could not be saved. Try again.",
        });
      }
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || deleteTarget.state.kind !== "failed" || !deleteTarget.state.item) return;
    setDeleteError(null);
    try {
      await remove.mutateAsync(deleteTarget.state.item.manuscript_id);
      updateRow(deleteTarget.key, { kind: "deleted" });
      setAnnouncement(`${deleteTarget.file.name}: upload deleted.`);
      const statusId = `library-add-status-${deleteTarget.key}`;
      setDeleteTarget(null);
      requestAnimationFrame(() => document.getElementById(statusId)?.focus());
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "The upload could not be deleted. Try again.");
    }
  }

  function addMore() {
    setFiles([]);
    setTypeErrors([]);
    setTitle("");
    setTeamName("");
    setAuthors([""]);
    setRows([]);
    rowsRef.current = [];
    setPhase("form");
    requestAnimationFrame(() => headingRef.current?.focus());
  }

  const doneCount = rows.filter((row) => isSettled(row.state)).length;
  const addedCount = rows.filter((r) => ["added", "processing", "added_anyway"].includes(r.state.kind)).length;
  const outcomeSummary = [
    `Added: ${addedCount}`,
    ...[
      ["already uploaded", rows.filter((r) => r.state.kind === "exact").length],
      ["waiting for your decision", rows.filter((r) => r.state.kind === "possible").length],
      ["could not be read", rows.filter((r) => r.state.kind === "failed").length],
      ["skipped", rows.filter((r) => r.state.kind === "skipped").length],
      ["not sent", rows.filter((r) => r.state.kind === "not_sent").length],
    ]
      .filter(([, count]) => (count as number) > 0)
      .map(([label, count]) => `${label}: ${count}`),
  ].join(". ");
  const openDecisions = rows.filter((row) => row.state.kind === "possible").length;
  const notSent = rows.filter((row) => row.state.kind === "not_sent").length;

  return (
    <div className="signal-route signal-page-flow signal-library-add">
      <header className="signal-route-header">
        <div>
          <nav className="signal-breadcrumb" aria-label="Breadcrumb">
            <Link to="/library">Manuscript Library</Link>
            <span aria-hidden="true">/</span>
            <span>Add manuscripts</span>
          </nav>
          <h1 ref={headingRef} tabIndex={-1}>Add manuscripts to the Library</h1>
          <p className="signal-route-header__intro">
            Add finished manuscripts so originality checks have something to compare against. Adding a
            manuscript does not check it and produces no grade or report.
          </p>
        </div>
      </header>

      <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">{announcement}</p>

      {phase === "form" && (
        <form
          className="signal-library-add__form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="signal-field">
            <span className="signal-library-add__label" id={`${fileInputId}-label`}>Manuscript files</span>
            <p id={filesHintId} className="signal-field-hint">
              PDF or DOCX. Choose one or more files; each becomes its own Library record. VERIDICAL reads the
              text only, so a scanned copy without selectable text cannot be added.
            </p>
            <div className="signal-library-dropzone" data-invalid={filesError ? true : undefined}>
              <input
                id={fileInputId}
                type="file"
                multiple
                accept={ACCEPT}
                className="sr-only"
                aria-labelledby={`${fileInputId}-label`}
                aria-describedby={filesError ? `${filesHintId} ${filesErrorId}` : filesHintId}
                aria-invalid={filesError ? true : undefined}
                onChange={(event) => {
                  chooseFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <label htmlFor={fileInputId} className="signal-button signal-button--secondary">
                Choose files
              </label>
              <span>{files.length ? plural(files.length, "file chosen", "files chosen") : "No files chosen yet."}</span>
            </div>
            {filesError && (
              <p id={filesErrorId} className="signal-field__error" role="alert">{filesError}</p>
            )}
            {typeErrors.map((name) => (
              <p key={name} className="signal-field__error">
                “{name}” is not a PDF or DOCX file, so it was not added to the list.
              </p>
            ))}
            {files.length > 0 && (
              <ul className="signal-library-chosen" aria-label="Chosen files">
                {files.map((file) => (
                  <li key={fileKey(file)}>
                    <span className="signal-library-filename">{file.name}</span>
                    <span>{formatSize(file.size)}</span>
                    <Button
                      type="button"
                      variant="quiet"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => removeFile(fileKey(file))}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <fieldset className="signal-library-add__fieldset">
            <legend>Details for every file</legend>
            <label className="signal-library-filter" htmlFor={programId}>
              <span>Program (optional)</span>
              <select id={programId} value={program} onChange={(event) => setProgram(event.target.value)}>
                <option value="">Not set</option>
                {(programs.data ?? []).map((p) => (
                  <option key={p.id} value={p.name}>{p.name}</option>
                ))}
              </select>
            </label>
            <div className="signal-field">
              <label htmlFor={yearId}>School year (optional)</label>
              <p id={yearHintId} className="signal-field-hint">For example, 2025-2026.</p>
              <input
                id={yearId}
                maxLength={20}
                value={schoolYear}
                aria-describedby={yearError ? `${yearHintId} ${yearErrorId}` : yearHintId}
                aria-invalid={yearError ? true : undefined}
                onChange={(event) => {
                  setSchoolYear(event.target.value);
                  setYearError(null);
                }}
              />
              {yearError && <p id={yearErrorId} className="signal-field__error" role="alert">{yearError}</p>}
            </div>
          </fieldset>

          {singleFile ? (
            <fieldset className="signal-library-add__fieldset">
              <legend>Details for this file (optional)</legend>
              <p className="signal-field-hint">
                Leave blank to use what VERIDICAL reads from the title page. You can edit these later. Other
                instructors cannot see these record fields, but they can read the manuscript.
              </p>
              <div className="signal-field">
                <label htmlFor={titleId}>Title</label>
                <input id={titleId} maxLength={500} value={title} onChange={(event) => setTitle(event.target.value)} />
              </div>
              <div className="signal-field">
                <label htmlFor={teamId}>Team name</label>
                <input id={teamId} maxLength={200} value={teamName} onChange={(event) => setTeamName(event.target.value)} />
              </div>
              <AuthorFields authors={authors} onChange={setAuthors} />
            </fieldset>
          ) : (
            detailsFilled && files.length > 1 && (
              <p role="status" className="signal-field-hint">
                Title, team, and author details apply to one file only, so they are set aside. They return if
                you go back to one file.
              </p>
            )
          )}

          <section className="signal-library-disclosure" aria-labelledby="library-add-disclosure">
            <h2 id="library-add-disclosure">Who can see what you add</h2>
            <ol>
              <li>Instructors at your school can have their checks compared against it.</li>
              <li>
                Signed-in instructors can read the full manuscript after it joins the Library. Its title, team,
                authors, and file name are hidden in record fields; program, school year and date added are visible.
              </li>
              <li>The full manuscript may contain names, its title, and other identifying details.</li>
              <li>Only you can edit or remove your record. Anonymous visitors cannot open Library manuscripts.</li>
              <li>Add only manuscripts your program allows you to share with other instructors here.</li>
              <li>
                The duplicate check when you add a file looks only at your own records. Matches with other
                instructors' records appear when a manuscript is checked.
              </li>
            </ol>
          </section>

          <div className="signal-form__actions">
            <Button type="submit" variant="brand">
              {files.length > 1
                ? `Add ${files.length} files to the Library`
                : files.length === 1
                  ? "Add 1 file to the Library"
                  : "Add files to the Library"}
            </Button>
            <Link to="/library" className="signal-button signal-button--quiet">Cancel</Link>
          </div>
        </form>
      )}

      {phase === "batch" && (
        <section className="signal-library-batch" aria-labelledby="library-batch-heading">
          <h2 id="library-batch-heading" ref={batchHeadingRef} tabIndex={-1}>
            {running
              ? `Adding ${plural(rows.length, "file", "files")}`
              : addedCount > 0
                ? `Finished: ${plural(addedCount, "file", "files")} added to the Library`
                : "Finished: nothing was added to the Library"}
          </h2>
          <p>{running ? `${doneCount} of ${rows.length} processed` : `${outcomeSummary}.`}</p>

          {rateLimited && (
            <Alert tone="warning" title="Paused: limit for adding files reached">
              <p>{plural(notSent, "file was", "files were")} not sent. Try again later.</p>
              <Button type="button" variant="secondary" disabled={running} onClick={retryUnsent}>
                Try unsent files again
              </Button>
            </Alert>
          )}

          <ol className="signal-library-batch__rows" aria-busy={running || undefined}>
            {rows.map((row) => {
              const state = row.state;
              const name = row.file.name;
              return (
                <li key={row.key} className="signal-library-batch__row">
                  <div className="signal-library-batch__head">
                    <span className="signal-library-filename">{name}</span>
                    <span id={`library-add-status-${row.key}`} tabIndex={-1}>
                      <RowStatus state={state} />
                    </span>
                  </div>
                  <RowDetail row={row} />
                  <div className="signal-library-batch__actions">
                    {(state.kind === "added" || state.kind === "processing") && (
                      <ActionLink to={`/library/${state.item.manuscript_id}`} variant="quiet" aria-label={`Open record for ${name}`}>
                        Open record
                      </ActionLink>
                    )}
                    {state.kind === "added_anyway" && (
                      <ActionLink to={`/library/${state.manuscriptId}`} variant="quiet" aria-label={`Open record for ${name}`}>
                        Open record
                      </ActionLink>
                    )}
                    {state.kind === "exact" && state.duplicate && (
                      <ActionLink to={`/library/${state.duplicate.manuscript_id}`} variant="quiet" aria-label={`Open existing record for ${name}`}>
                        Open existing record
                      </ActionLink>
                    )}
                    {state.kind === "possible" && (
                      <>
                        {state.duplicate && (
                          <ActionLink
                            to={`/library/compare?a=${state.item.manuscript_id}&b=${state.duplicate.manuscript_id}`}
                            variant="quiet"
                            aria-label={`Compare ${name} with your existing record`}
                          >
                            Compare the two
                          </ActionLink>
                        )}
                        <Button
                          type="button"
                          variant="secondary"
                          busy={state.deciding === "add"}
                          disabled={Boolean(state.deciding)}
                          aria-label={`Add anyway: ${name}`}
                          onClick={() => void decideRow(row, "add")}
                        >
                          Add anyway
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          busy={state.deciding === "skip"}
                          disabled={Boolean(state.deciding)}
                          aria-label={`Skip this copy: ${name}`}
                          onClick={() => setSkipTarget(row)}
                        >
                          Skip this copy
                        </Button>
                      </>
                    )}
                    {state.kind === "failed" && state.item && (
                      <Button type="button" variant="quiet" onClick={() => setDeleteTarget(row)}>
                        Delete upload
                      </Button>
                    )}
                    {state.kind === "not_sent" && !rateLimited && (
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={running}
                        onClick={() => {
                          updateRow(row.key, { kind: "queued" });
                          void runQueue();
                        }}
                      >
                        Try again
                      </Button>
                    )}
                  </div>
                  {state.kind === "possible" && state.error && <p role="alert">{state.error}</p>}
                </li>
              );
            })}
          </ol>

          {!running && (
            <div className="signal-library-batch__finish">
              {openDecisions > 0 && (
                <p>
                  {plural(openDecisions, "possible duplicate still needs", "possible duplicates still need")} your
                  decision. You can also decide later from the Library.
                </p>
              )}
              <div className="signal-form__actions">
                <Button type="button" variant="secondary" onClick={addMore}>Add more files</Button>
                <ActionLink to="/library" variant="primary">Go to Library</ActionLink>
              </div>
            </div>
          )}
        </section>
      )}

      {blocker.state === "blocked" && (
        <Dialog
          title={phase === "batch" ? "Stop adding files?" : "Leave without adding these files?"}
          onClose={() => blocker.reset()}
          actions={
            <>
              <Button type="button" variant="secondary" onClick={() => blocker.reset()}>
                {phase === "batch" ? "Keep adding" : "Keep choosing"}
              </Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  stopRequested.current = true;
                  blocker.proceed();
                }}
              >
                {phase === "batch" ? "Stop and leave" : "Leave"}
              </Button>
            </>
          }
        >
          {phase === "batch" ? (
            <p>
              {plural(unsent, "file has", "files have")} not been sent and will not be added. The file being sent
              now will still finish. Files already added stay in the Library, and possible duplicates wait for you
              there.
            </p>
          ) : (
            <p>The files you chose have not been added.</p>
          )}
        </Dialog>
      )}

      {skipTarget && skipTarget.state.kind === "possible" && (
        <Dialog
          title="Skip this copy?"
          onClose={() => setSkipTarget(null)}
          actions={
            <>
              <Button type="button" variant="secondary" onClick={() => setSkipTarget(null)}>Keep deciding</Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  const row = skipTarget;
                  setSkipTarget(null);
                  void decideRow(row, "skip");
                }}
              >
                Skip and delete this copy
              </Button>
            </>
          }
        >
          <p>
            The file “{skipTarget.file.name}” you just uploaded will not be added and will not be kept.
            {skipTarget.state.duplicate && ` Your existing record “${skipTarget.state.duplicate.label}” is not affected.`}
          </p>
        </Dialog>
      )}

      {deleteTarget && (
        <Dialog
          title="Delete this upload?"
          onClose={remove.isPending ? undefined : () => { setDeleteTarget(null); setDeleteError(null); }}
          actions={
            <>
              <Button type="button" variant="secondary" disabled={remove.isPending} onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button type="button" variant="danger" busy={remove.isPending} onClick={() => void confirmDelete()}>
                Delete upload
              </Button>
            </>
          }
        >
          <p>“{deleteTarget.file.name}” could not be read and is not in the Library. Deleting removes the stored file.</p>
          {deleteError && <p role="alert">{deleteError}</p>}
        </Dialog>
      )}

    </div>
  );
}

function RowStatus({ state }: { state: RowState }) {
  if (state.kind === "processing") return <LibraryEntryStatus status="processing" />;
  if (state.kind === "possible") return <LibraryEntryStatus status="needs_decision" />;
  if (state.kind === "failed") return <LibraryEntryStatus status="failed" />;
  const tone =
    state.kind === "added" || state.kind === "added_anyway"
      ? "done"
      : state.kind === "sending"
        ? "running"
        : state.kind === "not_sent" || state.kind === "rejected"
          ? "attention"
          : "neutral";
  return (
    <span className={`signal-process-status signal-process-status--${tone}`}>
      <span className="signal-process-status__mark" aria-hidden="true" />
      {rowStatusLabel(state)}
    </span>
  );
}

export function AuthorFields({
  authors,
  onChange,
}: {
  authors: string[];
  onChange: (next: string[]) => void;
}) {
  const baseId = useId();
  return (
    <fieldset className="signal-library-authors">
      <legend>Authors</legend>
      {authors.map((author, index) => (
        <div key={index} className="signal-library-authors__row">
          <label className="sr-only" htmlFor={`${baseId}-${index}`}>Author {index + 1}</label>
          <input
            id={`${baseId}-${index}`}
            maxLength={200}
            value={author}
            onChange={(event) => onChange(authors.map((a, i) => (i === index ? event.target.value : a)))}
          />
          {authors.length > 1 && (
            <Button
              type="button"
              variant="quiet"
              aria-label={`Remove author ${index + 1}`}
              onClick={() => onChange(authors.filter((_, i) => i !== index))}
            >
              Remove
            </Button>
          )}
        </div>
      ))}
      <Button type="button" variant="quiet" onClick={() => onChange([...authors, ""])}>
        Add author
      </Button>
    </fieldset>
  );
}
