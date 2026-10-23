// V-077 (D-028), new screen 4aa: add manuscripts to the Library. Adding
// stores a file as a comparison source and nothing else: no rubric, no
// grade, no report. This client sends files sequentially; shared server
// admission separately bounds covered preparation work within one process.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Link, useBlocker, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { isDocumentPreparationBusy } from "../api/documentPreparation";
import { StaleUploadContextError, UploadSessionUnavailableError } from "../api/uploadOutcome";
import { UploadSessionNotice } from "../components/UploadAttemptNotice";
import type { LibraryDuplicateRefOut, LibraryItemOut, LibrarySubmissionOut } from "../api/types";
import { usePrograms } from "../dashboard/useDashboard";
import { useRouteFocus } from "../routing/useRouteFocus";
import { claimFocusContinuation } from "../routing/focusContinuation";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { LibraryEntryStatus } from "./LibraryEntryStatus";
import { formatLibraryDate, normalizeSchoolYear } from "./libraryFormat";
import { useLibraryInvalidation } from "./useLibrary";
import { decideOwnedLibraryEntry, removeOwnedLibraryEntry, submitOwnedLibraryEntry } from "./libraryUploadRequests";
import { useLibraryUploadOwner, type LibraryWriteOperation } from "./useLibraryUploadOwner";

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
      unconfirmed?: boolean;
    }
  | { kind: "added_anyway"; manuscriptId: number }
  | { kind: "skipped" }
  | { kind: "failed"; message: string; item: LibraryItemOut | null; deletionUnconfirmed?: boolean }
  | { kind: "deleted" }
  | { kind: "not_attempted" }
  | { kind: "preparation_busy"; message: string }
  | { kind: "rate_limited"; message: string }
  | { kind: "result_unknown" }
  | { kind: "rejected"; message: string };

type StopCause = "preparation_busy" | "rate_limited" | "result_unknown" | "session_unavailable";

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
      return state.unconfirmed ? "Decision result not confirmed" : "Possible duplicate";
    case "skipped":
      return "Skipped";
    case "failed":
      return state.deletionUnconfirmed ? "Deletion result not confirmed" : "Could not read";
    case "deleted":
      return "Upload deleted";
    case "not_attempted":
      return "Not attempted";
    case "preparation_busy":
      return "Not added: preparation unavailable";
    case "rate_limited":
      return "Not added: upload limit reached";
    case "result_unknown":
      return "Result not confirmed";
    case "rejected":
      return "Could not add";
  }
}

function isSettled(state: RowState): boolean {
  if (state.kind === "possible" && state.unconfirmed || state.kind === "failed" && state.deletionUnconfirmed) return false;
  return !["queued", "sending", "not_attempted", "result_unknown"].includes(state.kind);
}

function canRetry(state: RowState): boolean {
  return ["not_attempted", "preparation_busy", "rate_limited"].includes(state.kind);
}

function usableItem(item: LibraryItemOut | null): item is LibraryItemOut {
  return Boolean(item && Number.isSafeInteger(item.manuscript_id) && item.manuscript_id > 0
    && typeof item.label === "string" && Array.isArray(item.proposed_fields)
    && item.proposed_fields.every((field) => typeof field === "string"));
}

function outcomeFromSubmission(result: LibrarySubmissionOut): RowState {
  // A TypeScript response type cannot establish that a response was usable.
  if (!result || typeof result.message !== "string") return { kind: "result_unknown" };
  if (result.duplicate_of !== null && (!result.duplicate_of
    || !Number.isSafeInteger(result.duplicate_of.manuscript_id) || result.duplicate_of.manuscript_id <= 0
    || typeof result.duplicate_of.label !== "string"
    || (result.duplicate_of.added_at !== null && (typeof result.duplicate_of.added_at !== "string"
      || !Number.isFinite(Date.parse(result.duplicate_of.added_at)))))) return { kind: "result_unknown" };
  if (result.item !== null && !usableItem(result.item)) return { kind: "result_unknown" };
  if (result.outcome === "exact_duplicate" && result.item === null) {
    return { kind: "exact", duplicate: result.duplicate_of, message: result.message };
  }
  if (result.outcome === "possible_duplicate" && result.item?.status === "needs_decision") {
    return { kind: "possible", item: result.item, duplicate: result.duplicate_of };
  }
  if (result.outcome === "failed" && (result.item === null || result.item.status === "failed")) return { kind: "failed", message: result.message, item: result.item };
  if (result.outcome === "added" && result.item?.status === "processing") return { kind: "processing", item: result.item };
  if (result.outcome === "added" && result.item?.status === "active") return { kind: "added", item: result.item };
  return { kind: "result_unknown" };
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
    if (state.unconfirmed) return <p>The decision result is not confirmed. Review this record in the Library before trying again.</p>;
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
  if (state.kind === "failed") return <p>{state.deletionUnconfirmed ? "The deletion result is not confirmed. Review this record in the Library." : state.message}</p>;
  if (state.kind === "deleted") return <p>The unreadable upload was deleted.</p>;
  if (state.kind === "not_attempted") return <p>This file has not been sent.</p>;
  if (state.kind === "preparation_busy" || state.kind === "rate_limited") return <p>{state.message}</p>;
  if (state.kind === "result_unknown") return <p>VERIDICAL could not confirm whether this attempt added a document. Check the Library before adding this file again.</p>;
  if (state.kind === "rejected") return <p>{state.message}</p>;
  return null;
}

export function SignalLibraryAddPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const batchHeadingRef = useRef<HTMLHeadingElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const sessionNoticeRef = useRef<HTMLDivElement>(null);
  const rowActionFocusRef = useRef<HTMLElement>(null);
  const navigate = useNavigate();
  useRouteFocus("Add manuscripts - VERIDICAL", headingRef);
  const invalidateLibrary = useLibraryInvalidation();
  const owner = useLibraryUploadOwner();
  const isOwnedContextCurrent = owner.session.isCurrent;
  const ownsContext = owner.session.ownsContext;
  const canWrite = ownsContext && owner.session.status === "ready" && !owner.busy;
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
  const [runningTotal, setRunningTotal] = useState(0);
  const [pauseCause, setPauseCause] = useState<StopCause | null>(null);
  const [confirmNewUpload, setConfirmNewUpload] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Row | null>(null);
  const [skipTarget, setSkipTarget] = useState<Row | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [sessionRecovery, setSessionRecovery] = useState("");
  const rowsRef = useRef<Row[]>([]);
  const stopRequested = useRef(false);
  const submissionRef = useRef<{ program?: string; schoolYear?: string; title?: string; teamName?: string; authors?: string[] }>({});

  const singleFile = files.length === 1;
  const detailsFilled = Boolean(title.trim() || teamName.trim() || authors.some((a) => a.trim()));
  const formDirty = ownsContext && phase === "form" && (files.length > 0 || detailsFilled || Boolean(schoolYear.trim()));
  const batchUnsafe = ownsContext && phase === "batch" && owner.busy;

  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    owner.session.isCurrent() && (formDirty || batchUnsafe) && currentLocation.pathname !== nextLocation.pathname,
  );

  useLayoutEffect(() => {
    if (ownsContext) return;
    stopRequested.current = true;
    submissionRef.current = {};
    rowsRef.current = [];
    rowActionFocusRef.current = null;
    setFiles([]); setTypeErrors([]); setFilesError(null); setProgram(""); setSchoolYear("");
    setYearError(null); setTitle(""); setTeamName(""); setAuthors([""]); setRows([]);
    setRunning(false); setRunningTotal(0); setPauseCause(null); setPhase("form");
    setConfirmNewUpload(false); setSkipTarget(null); setDeleteTarget(null); setDeleteError(null);
    setAnnouncement(""); setSessionRecovery("");
    const active = document.activeElement;
    if (active === document.body || active && pageRef.current?.contains(active)) sessionNoticeRef.current?.focus();
  }, [ownsContext]);

  useEffect(() => {
    if (!ownsContext && blocker.state === "blocked") blocker.proceed();
  }, [ownsContext, blocker]);

  useEffect(() => {
    if (!formDirty && !batchUnsafe) return;
    function beforeUnload(event: BeforeUnloadEvent) {
      if (!isOwnedContextCurrent()) return;
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [formDirty, batchUnsafe, isOwnedContextCurrent]);

  function updateRow(key: string, state: RowState, operation: LibraryWriteOperation) {
    if (!operation.isCurrent()) return;
    rowsRef.current = rowsRef.current.map((row) => (row.key === key ? { ...row, state } : row));
    setRows(rowsRef.current);
  }

  function chooseFiles(list: FileList | null) {
    if (!list || !canWrite) return;
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
    if (!canWrite) return;
    setFiles((current) => current.filter((file) => fileKey(file) !== key));
  }

  function normalizedYear(): string | null {
    return normalizeSchoolYear(schoolYear);
  }

  async function sendOne(row: Row, operation: LibraryWriteOperation): Promise<"ok" | StopCause | "retired"> {
    let admitted = false;
    try {
      const result = await submitOwnedLibraryEntry({ file: row.file, ...submissionRef.current }, operation.scope, () => {
        operation.admitted();
        admitted = true;
        updateRow(row.key, { kind: "sending" }, operation);
      });
      if (!operation.isCurrent()) return "retired";
      const state = outcomeFromSubmission(result);
      updateRow(row.key, state, operation);
      if (state.kind === "result_unknown") return "result_unknown";
      const done = rowsRef.current.filter((r) => isSettled(r.state)).length;
      setAnnouncement(`${row.file.name}: ${rowStatusLabel(state)}. ${done} of ${rowsRef.current.length} done.`);
      return "ok";
    } catch (error) {
      if (error instanceof StaleUploadContextError || !operation.isCurrent()) return "retired";
      if (!admitted && error instanceof UploadSessionUnavailableError) {
        updateRow(row.key, { kind: "not_attempted" }, operation);
        return "session_unavailable";
      }
      if (isDocumentPreparationBusy(error)) {
        updateRow(row.key, { kind: "preparation_busy", message: error.message }, operation);
        return "preparation_busy";
      }
      if (error instanceof ApiError && error.status === 429) {
        updateRow(row.key, { kind: "rate_limited", message: error.message }, operation);
        return "rate_limited";
      }
      if (error instanceof ApiError && error.status < 500 && error.status !== 408) {
        updateRow(row.key, { kind: "rejected", message: error.message }, operation);
        return "ok";
      }
      updateRow(row.key, { kind: "result_unknown" }, operation);
      return "result_unknown";
    }
  }

  async function runQueue(operation: LibraryWriteOperation) {
    if (!operation.isCurrent()) return;
    setRunning(true);
    setRunningTotal(rowsRef.current.filter((row) => row.state.kind === "queued").length);
    setPauseCause(null);
    setSessionRecovery("");
    let stopped: StopCause | null = null;
    let stoppedFilename = "";
    try {
      for (const row of rowsRef.current) {
        if (stopRequested.current || !operation.isCurrent()) return;
        const current = rowsRef.current.find((r) => r.key === row.key);
        if (!current || current.state.kind !== "queued") continue;
        const result = await sendOne(current, operation);
        if (!operation.isCurrent() || result === "retired") return;
        if (result !== "ok") {
          stopped = result;
          stoppedFilename = current.file.name;
          setPauseCause(result);
          for (const remaining of rowsRef.current) {
            if (remaining.state.kind === "queued") updateRow(remaining.key, { kind: "not_attempted" }, operation);
          }
          break;
        }
      }
      if (!operation.isCurrent()) return;
      invalidateLibrary();
      const settled = rowsRef.current;
      const added = settled.filter((r) => ["added", "added_anyway"].includes(r.state.kind)).length;
      const unknown = settled.filter((r) => r.state.kind === "result_unknown").length;
      const preparing = settled.filter((r) => r.state.kind === "processing").length;
      const waiting = settled.filter((r) => r.state.kind === "possible").length;
      const unreadable = settled.filter((r) => r.state.kind === "failed").length;
      const parts = [added ? `${added} added` : "", waiting ? `${plural(waiting, "possible duplicate", "possible duplicates")} waiting for your decision` : "", unreadable ? `${unreadable} could not be read` : ""].filter(Boolean);
      const outcome = parts.length ? ` ${parts.join(", ")}.` : "";
      if (stopped === "session_unavailable") setAnnouncement(`The batch is paused until your sign-in status is confirmed.${outcome}`);
      else if (stopped === "preparation_busy") setAnnouncement(`Paused: document preparation unavailable.${outcome} Only remaining eligible files can be tried again. ${unknown ? `${unknown} upload results are still not confirmed.` : ""}`);
      else if (stopped === "rate_limited") setAnnouncement(`Paused: limit for adding files reached.${outcome} ${unknown ? `${unknown} upload results are still not confirmed.` : ""}`);
      else if (stopped === "result_unknown") setAnnouncement(`${stoppedFilename}: result not confirmed. The batch is paused. Review the results before adding this file again.${outcome} ${unknown} upload results are still not confirmed.`);
      else if (unknown) setAnnouncement(`Remaining upload attempts have ended. ${unknown} upload results are still not confirmed.${outcome} Check the Library before adding those files again.`);
      else if (preparing) setAnnouncement(`Upload requests ended. ${preparing} files are still being prepared.${outcome}`);
      else setAnnouncement(parts.length ? `Finished.${outcome}` : "Finished.");
    } finally {
      if (operation.isCurrent()) setRunning(false);
      owner.finish(operation);
    }
  }

  function queueFocus(operation: LibraryWriteOperation, target: () => HTMLElement | null, claim = claimFocusContinuation()) {
    requestAnimationFrame(() => {
      if (operation.ownsContinuation()) claim.focus(target());
      else claim.dispose();
    });
  }

  function submit() {
    if (!canWrite || !owner.session.isCurrent()) return;
    if (files.length === 0) {
      setFilesError("Choose at least one PDF or DOCX file.");
      const claim = claimFocusContinuation();
      requestAnimationFrame(() => { if (owner.session.isCurrent()) claim.focus(document.getElementById(fileInputId)); else claim.dispose(); });
      return;
    }
    const year = normalizedYear();
    if (year === null) {
      setYearError("Enter the school year as two consecutive years, for example 2025-2026.");
      const claim = claimFocusContinuation();
      requestAnimationFrame(() => { if (owner.session.isCurrent()) claim.focus(document.getElementById(yearId)); else claim.dispose(); });
      return;
    }
    const operation = owner.begin("batch");
    if (!operation) return;
    setYearError(null);
    submissionRef.current = {
      program: program || undefined, schoolYear: year || undefined,
      ...(singleFile ? { title: title.trim() || undefined, teamName: teamName.trim() || undefined,
        authors: authors.map((a) => a.trim()).filter(Boolean) } : {}),
    };
    stopRequested.current = false;
    rowsRef.current = files.map((file) => ({ key: fileKey(file), file, state: { kind: "queued" } }));
    setRows(rowsRef.current);
    setPhase("batch");
    queueFocus(operation, () => batchHeadingRef.current);
    void runQueue(operation);
  }

  function retryRemaining() {
    if (!canWrite) return;
    const operation = owner.begin("batch");
    if (!operation) return;
    for (const row of rowsRef.current) if (canRetry(row.state)) updateRow(row.key, { kind: "queued" }, operation);
    stopRequested.current = false;
    queueFocus(operation, () => batchHeadingRef.current);
    void runQueue(operation);
  }

  function definiteWriteRejection(error: unknown) {
    return error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408;
  }

  async function decideRow(row: Row, action: "add" | "skip") {
    if (!canWrite || row.state.kind !== "possible" || row.state.unconfirmed) return;
    const operation = owner.begin("decision");
    if (!operation) return;
    const prior = row.state;
    const item = prior.item;
    const focus = claimFocusContinuation();
    let focusQueued = false;
    let admitted = false;
    try {
      await decideOwnedLibraryEntry(item.manuscript_id, action, operation.scope, () => {
        operation.admitted(); admitted = true;
        updateRow(row.key, { ...prior, deciding: action, error: undefined }, operation);
      });
      if (!operation.isCurrent()) return;
      updateRow(row.key, action === "add" ? { kind: "added_anyway", manuscriptId: item.manuscript_id } : { kind: "skipped" }, operation);
      setAnnouncement(`${row.file.name}: ${action === "add" ? "added anyway" : "skipped, this copy was not kept"}.`);
      invalidateLibrary(item.manuscript_id, action === "skip");
      if (action === "skip") { setSkipTarget(null); focus.dispose(); }
      else { focusQueued = true; queueFocus(operation, () => document.getElementById(`library-add-status-${row.key}`), focus); }
    } catch (error) {
      focus.dispose();
      if (error instanceof StaleUploadContextError || !operation.isCurrent()) return;
      const unconfirmed = admitted && !definiteWriteRejection(error);
      updateRow(row.key, { ...prior, deciding: undefined, unconfirmed,
        error: unconfirmed ? "The decision result is not confirmed. Review this record in the Library before trying again."
          : error instanceof ApiError ? error.message : "Check your sign-in status before continuing." }, operation);
      if (action === "skip") setSkipTarget(null);
    } finally { if (!focusQueued) focus.dispose(); owner.finish(operation); }
  }

  function closeSkip() {
    if (owner.operation?.kind === "decision" && !owner.cancelPreflight()) return;
    setSkipTarget(null);
  }

  function closeDelete() {
    if (owner.operation?.kind === "remove" && !owner.cancelPreflight()) return;
    setDeleteTarget(null); setDeleteError(null);
  }

  async function confirmDelete() {
    if (!canWrite || !deleteTarget || deleteTarget.state.kind !== "failed" || !deleteTarget.state.item) return;
    const latest = rowsRef.current.find(row => row.key === deleteTarget.key);
    if (latest?.state.kind !== "failed" || latest.state.deletionUnconfirmed) return;
    const operation = owner.begin("remove");
    if (!operation) return;
    const row = latest;
    const prior = latest.state;
    setDeleteError(null);
    let admitted = false;
    try {
      await removeOwnedLibraryEntry(prior.item!.manuscript_id, operation.scope, () => { operation.admitted(); admitted = true; });
      if (!operation.isCurrent()) return;
      updateRow(row.key, { kind: "deleted" }, operation);
      setAnnouncement(`${row.file.name}: upload deleted.`);
      invalidateLibrary(prior.item!.manuscript_id, true);
      setDeleteTarget(null);
    } catch (error) {
      if (error instanceof StaleUploadContextError || !operation.isCurrent()) return;
      const unconfirmed = admitted && !definiteWriteRejection(error);
      if (unconfirmed) updateRow(row.key, { ...prior, deletionUnconfirmed: true }, operation);
      setDeleteError(unconfirmed ? "The deletion result is not confirmed. Review this record in the Library."
        : error instanceof ApiError ? error.message : "Check your sign-in status before continuing.");
    } finally { owner.finish(operation); }
  }

  function addMore() {
    if (!canWrite || !owner.session.isCurrent()) return;
    owner.retire();
    const focus = claimFocusContinuation();
    setFiles([]); setTypeErrors([]); setFilesError(null); setYearError(null);
    setTitle(""); setTeamName(""); setAuthors([""]); setRows([]); setPauseCause(null);
    setConfirmNewUpload(false); setSessionRecovery("");
    rowsRef.current = []; submissionRef.current = {};
    setPhase("form");
    requestAnimationFrame(() => { if (owner.session.isCurrent()) focus.focus(headingRef.current); else focus.dispose(); });
  }

  async function checkSignIn() {
    if (!ownsContext || owner.busy) return;
    const focus = claimFocusContinuation();
    try {
      await owner.session.verify();
      if (!owner.session.isCurrent()) return;
      const eligible = rowsRef.current.some(row => canRetry(row.state));
      setAnnouncement("");
      setSessionRecovery(phase === "batch" ? eligible
        ? "Sign-in status confirmed. Review these results, then choose Try remaining files to continue."
        : "Sign-in status confirmed. Review these results before continuing."
        : files.length ? "Sign-in status confirmed. Review the selected files before continuing."
          : "Sign-in status confirmed. You can choose files for a new upload.");
      focus.focus(phase === "batch" ? batchHeadingRef.current : headingRef.current);
    } catch { /* The session notice owns recovery; a GET never resumes writes. */ }
    finally { focus.dispose(); }
  }

  function closeForm() {
    if (!owner.session.isCurrent()) { stopRequested.current = true; owner.retire(); }
    void navigate("/library");
  }

  const doneCount = rows.filter((row) => isSettled(row.state)).length;
  const addedCount = rows.filter((r) => ["added", "added_anyway"].includes(r.state.kind)).length;
  const unknownCount = rows.filter((r) => r.state.kind === "result_unknown").length;
  const decisionUnknownCount = rows.filter((r) => r.state.kind === "possible" && r.state.unconfirmed).length;
  const deletionUnknownCount = rows.filter((r) => r.state.kind === "failed" && r.state.deletionUnconfirmed).length;
  const anyUnconfirmed = unknownCount + decisionUnknownCount + deletionUnknownCount > 0;
  const preparingCount = rows.filter((r) => r.state.kind === "processing").length;
  const outcomeSummary = [
    `${unknownCount > 0 ? "Confirmed added" : "Added"}: ${addedCount}`,
    ...[
      ["already uploaded", rows.filter((r) => r.state.kind === "exact").length],
      ["waiting for your decision", rows.filter((r) => r.state.kind === "possible" && !r.state.unconfirmed).length],
      ["could not be read", rows.filter((r) => r.state.kind === "failed").length],
      ["skipped", rows.filter((r) => r.state.kind === "skipped").length],
      ["still being prepared", preparingCount],
      ["results not confirmed", unknownCount],
      ["decision results not confirmed", decisionUnknownCount],
      ["deletion results not confirmed", deletionUnknownCount],
      ["not attempted", rows.filter((r) => r.state.kind === "not_attempted").length],
      ["preparation unavailable", rows.filter((r) => r.state.kind === "preparation_busy").length],
      ["upload limit reached", rows.filter((r) => r.state.kind === "rate_limited").length],
      ["could not add", rows.filter((r) => r.state.kind === "rejected").length],
    ]
      .filter(([, count]) => (count as number) > 0)
      .map(([label, count]) => `${label}: ${count}`),
  ].join(". ");
  const openDecisions = rows.filter((row) => row.state.kind === "possible" && !row.state.unconfirmed).length;
  const retryable = rows.filter((row) => canRetry(row.state)).length;
  const noticeStatus = !ownsContext ? "lost" : owner.session.status === "ready" ? undefined : owner.session.status;
  const deletionUnconfirmed = rows.some(row => row.key === deleteTarget?.key && row.state.kind === "failed" && row.state.deletionUnconfirmed);
  const deleting = owner.operation?.kind === "remove" && owner.operation.stage === "sent";
  const deciding = owner.operation?.kind === "decision" && owner.operation.stage === "sent";

  return (
    <div ref={pageRef} className="signal-route signal-page-flow signal-library-add">
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

      <div ref={sessionNoticeRef} tabIndex={-1} aria-label="Upload sign-in status" hidden={!noticeStatus && !sessionRecovery}>
        {noticeStatus && <UploadSessionNotice status={noticeStatus} onCheck={() => { void checkSignIn(); }} onClose={closeForm} />}
        {!noticeStatus && sessionRecovery && <p role="status">{sessionRecovery}</p>}
      </div>

      {ownsContext && <div className="contents">

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
          <fieldset disabled={!canWrite} className="contents">
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
                disabled={!canWrite}
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
              <label htmlFor={fileInputId} aria-disabled={!canWrite || undefined} className="signal-button signal-button--secondary">
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
            <Button type="submit" variant="brand" disabled={!canWrite}>
              {files.length > 1
                ? `Add ${files.length} files to the Library`
                : files.length === 1
                  ? "Add 1 file to the Library"
                  : "Add files to the Library"}
            </Button>
            <Link to="/library" className="signal-button signal-button--quiet">Cancel</Link>
          </div>
          </fieldset>
        </form>
      )}

      {phase === "batch" && (
        <section className="signal-library-batch" aria-labelledby="library-batch-heading">
          <h2 id="library-batch-heading" ref={batchHeadingRef} tabIndex={-1}>
            {running
              ? `Adding ${plural(runningTotal, "file", "files")}`
              : pauseCause === "session_unavailable"
                ? owner.session.status === "ready" ? "Paused: choose whether to continue" : "Paused: sign-in status not confirmed"
              : pauseCause === "preparation_busy"
                ? "Paused: document preparation unavailable"
              : pauseCause === "rate_limited"
                ? "Paused: limit for adding files reached"
              : unknownCount > 0
                ? retryable > 0 ? "Paused: an upload result was not confirmed" : "Upload results need review"
              : preparingCount > 0
                ? "Upload results: some files are still being prepared"
              : decisionUnknownCount + deletionUnknownCount > 0
                ? "Library changes need review"
              : addedCount > 0
                ? `Finished: ${plural(addedCount, "file", "files")} added to the Library`
                : "Finished: nothing was added to the Library"}
          </h2>
          <p>{running ? `${doneCount} confirmed upload responses in this batch. ${rows.length - doneCount} results pending or unconfirmed.` : `${outcomeSummary}.`}</p>

          {pauseCause === "rate_limited" && (
            <Alert tone="warning" title="Paused: limit for adding files reached">
              <p>This batch stopped at the upload limit. Earlier results remain below. Try remaining eligible files later.</p>
            </Alert>
          )}
          {pauseCause === "preparation_busy" && (
            <Alert tone="warning" title="Document preparation unavailable">
              <p>This batch is paused. The rejected attempt and files not attempted can be tried again later. Earlier results remain below.</p>
            </Alert>
          )}
          {unknownCount > 0 && (
            <Alert tone="warning" title="Some upload results are not confirmed">
              <p>Check the collection and “Your uploads not in the Library”. An upload may still be being prepared or waiting for a duplicate decision.</p>
              <p>Leaving this page does not save these local batch results. It does not cancel or settle an unconfirmed request.</p>
            </Alert>
          )}
          {retryable > 0 && (
            <div className="signal-group-flow">
              <p>Try only files not attempted or rejected before document preparation. Unconfirmed results will not be sent again.</p>
              <Button type="button" variant="secondary" disabled={!canWrite} onClick={retryRemaining}>
                Try remaining files
              </Button>
            </div>
          )}

          <ol className="signal-library-batch__rows" aria-busy={owner.busy || undefined}>
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
                        {state.unconfirmed && <ActionLink to={`/library/${state.item.manuscript_id}`} variant="quiet" aria-label={`Review decision result for ${name}`}>Open record</ActionLink>}
                        {!state.unconfirmed && state.duplicate && (
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
                          disabled={!canWrite || Boolean(state.deciding) || state.unconfirmed}
                          aria-label={`Add anyway: ${name}`}
                          onClick={() => void decideRow(row, "add")}
                        >
                          Add anyway
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          busy={state.deciding === "skip"}
                          disabled={!canWrite || Boolean(state.deciding) || state.unconfirmed}
                          aria-label={`Skip this copy: ${name}`}
                          onClick={() => { rowActionFocusRef.current = document.getElementById(`library-add-status-${row.key}`); setSkipTarget(row); }}
                        >
                          Skip this copy
                        </Button>
                      </>
                    )}
                    {state.kind === "failed" && state.item && (
                      <Button type="button" variant="quiet" disabled={!canWrite || state.deletionUnconfirmed} onClick={() => { rowActionFocusRef.current = document.getElementById(`library-add-status-${row.key}`); setDeleteTarget(row); }}>
                        Delete upload
                      </Button>
                    )}
                    {state.kind === "failed" && state.deletionUnconfirmed && <ActionLink to="/library" variant="quiet">Go to Library</ActionLink>}
                    {state.kind === "result_unknown" && (
                      <ActionLink to="/library" variant="quiet" aria-label={`Go to Library to review the result for ${name}`}>
                        Go to Library
                      </ActionLink>
                    )}
                  </div>
                  {state.kind === "possible" && state.error && !state.unconfirmed && <p role="alert">{state.error}</p>}
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
                <Button type="button" variant="secondary" disabled={!canWrite} onClick={() => anyUnconfirmed ? setConfirmNewUpload(true) : addMore()}>
                  {anyUnconfirmed ? "Start another upload" : "Add more files"}
                </Button>
                <ActionLink to="/library" variant="primary">Go to Library</ActionLink>
              </div>
            </div>
          )}
        </section>
      )}

      {confirmNewUpload && (
        <Dialog
          title="Start another upload?"
          onClose={() => setConfirmNewUpload(false)}
          actions={<>
            <Button type="button" variant="secondary" onClick={() => setConfirmNewUpload(false)}>Keep these results</Button>
            <Button type="button" variant="secondary" disabled={!canWrite} onClick={addMore}>Start another upload</Button>
          </>}
        >
          <p>An earlier result is still unconfirmed. Starting another upload clears this page’s local results; it does not cancel or settle that request. Check the Library before trying that action again.</p>
        </Dialog>
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
                  owner.retire();
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
              Files not yet sent will not be sent after you leave. A request already sent may still finish.
              Leaving does not cancel or confirm its result. Files already added stay in the Library,
              and possible duplicates remain available there.
            </p>
          ) : (
            <p>The files you chose have not been added.</p>
          )}
        </Dialog>
      )}

      {skipTarget && skipTarget.state.kind === "possible" && (
        <Dialog
          title="Skip this copy?"
          onClose={deciding ? undefined : closeSkip}
          fallbackFocusRef={rowActionFocusRef}
          actions={
            <>
              <Button type="button" variant="secondary" disabled={deciding} onClick={closeSkip}>Keep deciding</Button>
              <Button
                type="button"
                variant="danger"
                disabled={!canWrite}
                busy={deciding}
                onClick={() => {
                  const row = skipTarget;
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
          onClose={deleting ? undefined : closeDelete}
          fallbackFocusRef={rowActionFocusRef}
          actions={
            <>
              <Button type="button" variant="secondary" disabled={deleting} onClick={closeDelete}>
                Cancel
              </Button>
              <Button type="button" variant="danger" disabled={!canWrite || deletionUnconfirmed} busy={deleting} onClick={() => void confirmDelete()}>
                Delete upload
              </Button>
            </>
          }
        >
          <p>“{deleteTarget.file.name}” could not be read and is not in the Library. Deleting removes the stored file.</p>
          {deleteError && <p role="alert">{deleteError}</p>}
          {deletionUnconfirmed && <ActionLink to="/library" variant="quiet">Go to Library</ActionLink>}
        </Dialog>
      )}

      </div>}

    </div>
  );
}

function RowStatus({ state }: { state: RowState }) {
  if (state.kind === "processing") return <LibraryEntryStatus status="processing" />;
  if (state.kind === "possible" && !state.unconfirmed) return <LibraryEntryStatus status="needs_decision" />;
  if (state.kind === "failed" && !state.deletionUnconfirmed) return <LibraryEntryStatus status="failed" />;
  const unconfirmed = state.kind === "possible" && state.unconfirmed || state.kind === "failed" && state.deletionUnconfirmed;
  const tone =
    state.kind === "added" || state.kind === "added_anyway"
      ? "done"
      : state.kind === "sending"
        ? "running"
        : unconfirmed || ["preparation_busy", "rate_limited", "result_unknown", "rejected"].includes(state.kind)
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
