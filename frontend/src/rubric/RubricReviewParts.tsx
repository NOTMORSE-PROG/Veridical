// BUG-566 (screen 4d): the instructor checks each level description against
// the uploaded format before the format can grade. Matched levels stay
// quiet; only the ones VERIDICAL could not match ask for a decision.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { RubricAcknowledgementReason, RubricIssueDetail, RubricLevel, RubricSourceSummary } from "../api/types";
import { RUBRIC_KEEP_NOTE_MAX_LENGTH, RUBRIC_KEEP_NOTE_MIN_CHARS, RUBRIC_REVIEW_STATE_LABELS } from "../config/ui";
import { Button } from "../ui/Button";
import { useShowSourcePage, type SourceTarget } from "./sourcePage";
import { useRubric } from "./useRubric";
import {
  columnLevelName, formatReviewLabel, formatReviewState, issueCopy, issueStatus, itemsToCheck, keepNoteRequired, keepReasonLabel,
  keepReasonsFor, levelAnchorId, levelStatus, noteHasContent, rowLocation, shortDate, sourceLocation, type FormatReviewState, type LevelStatus,
  type PendingAcknowledgement,
} from "./rubricReview";

function Icon({ children }: { children: ReactNode }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">{children}</svg>;
}
const EyeIcon = () => <Icon><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.75" /></Icon>;
const CheckIcon = () => <Icon><path d="m5 12.5 4.5 4.5L19 7.5" /></Icon>;
const CheckCircleIcon = () => <Icon><circle cx="12" cy="12" r="8.5" /><path d="m8.5 12.25 2.5 2.5 4.75-5" /></Icon>;
const DashIcon = () => <Icon><path d="M7 12h10" /></Icon>;

const TAGS: Record<LevelStatus["kind"], { label: string; icon: () => ReactNode }> = {
  check: { label: "Check this", icon: EyeIcon },
  kept_pending: { label: "Kept as written", icon: CheckCircleIcon },
  kept: { label: "Kept as written", icon: CheckCircleIcon },
  matches: { label: "Matches the document", icon: CheckIcon },
  row_unchecked: { label: "Not checked separately", icon: DashIcon },
  not_checked: { label: "Not checked against the document", icon: DashIcon },
};

export function LevelStatusTag({ kind }: { kind: LevelStatus["kind"] }) {
  const tag = TAGS[kind];
  return <span className={`signal-level-tag signal-level-tag--${kind.replace("_", "-")}`}><tag.icon /><span>{tag.label}</span></span>;
}

export function RubricReviewStatus({ total, open, unsaved, refused, readOnly, isActive, state, onFirst, onSave }: {
  total: number;
  open: number;
  // Items kept on this page and not saved yet.
  unsaved: number;
  // Items a refused save of an active format would have left to check.
  refused: number;
  readOnly: boolean;
  isActive: boolean;
  state: FormatReviewState;
  onFirst: () => void;
  onSave: () => void;
}) {
  if (state.kind === "legacy") return <div className="signal-review-status" role="status">
    <p>This version was prepared before VERIDICAL compared level descriptions with the document. Its level descriptions have not been checked.</p>
  </div>;
  const saveWord = isActive ? "save your changes" : "save a draft, or confirm and activate to save and activate together";
  let text: string;
  if (refused > 0) text = `Nothing was saved. These changes would leave ${itemsToCheck(refused)}; the list below names them.`;
  else if (readOnly) text = open
    ? `${isActive ? `${RUBRIC_REVIEW_STATE_LABELS.activeWithItems}. ` : ""}${itemsToCheck(open)} in this version. This version is locked, so corrections go into a new version.`
    : total ? "Every item VERIDICAL raised in this version was kept or settled." : "Every level description VERIDICAL could place matches a cell in the document.";
  else if (open) text = isActive
    ? `${RUBRIC_REVIEW_STATE_LABELS.activeWithItems}. ${itemsToCheck(open)}; changes can be saved once every item is kept.`
    : `${itemsToCheck(open)} before this format can be activated.`;
  else if (unsaved) text = `No items to check. ${unsaved} kept ${unsaved === 1 ? "item is" : "items are"} not saved yet: ${saveWord}.`;
  else if (total) text = `${RUBRIC_REVIEW_STATE_LABELS.flaggedAllChecked}.${isActive ? "" : " You can activate this format."}`;
  else text = "Every level description VERIDICAL could place matches a cell in the document. Read each criterion before you activate.";
  const busy = refused > 0 || open > 0 || unsaved > 0;
  return <div className={`signal-review-status${busy ? " is-open" : ""}`} role="status">
    <div className="signal-copy-flow">
      <p>{text}</p>
      {open > 0 && !readOnly && refused === 0 && <p className="signal-review-status__hint">
        You cannot correct a level's wording on this page yet. For now, keep the wording with a note, remove the criterion,
        {isActive ? " or upload a corrected copy of the format as a new version." : " or leave this format inactive and upload a corrected copy."}
      </p>}
    </div>
    {open > 0 && refused === 0 && <Button variant="secondary" onClick={onFirst}>Go to first item</Button>}
    {open === 0 && unsaved > 0 && refused === 0 && !readOnly && <Button variant="secondary" onClick={onSave}>Go to save</Button>}
  </div>;
}

/** A format version's live review state as a tag, for pages that list
 * versions without their issues. Formats prepared before items were
 * recorded show nothing here; their review screen lists the parser notes. */
export function FormatReviewStateTag({ rubricId }: { rubricId: number }) {
  const { data } = useRubric(rubricId);
  if (!data) return null;
  const state = formatReviewState(data);
  const label = formatReviewLabel(state);
  if (!label) return null;
  const tone = state.kind === "to_check" || state.kind === "active_with_items" ? "to-check" : "settled";
  return <span className={`signal-version-state signal-version-state--${tone}`}>{label}</span>;
}

export function ReviewItemNavigator({ items, currentId, unsaved, refused, readOnly, onGo, onSave, onShowRefusal }: {
  items: readonly RubricIssueDetail[];
  currentId: number | null;
  unsaved: number;
  refused: number;
  readOnly: boolean;
  onGo: (issue: RubricIssueDetail) => void;
  onSave: () => void;
  onShowRefusal: () => void;
}) {
  if (refused > 0) return <nav className="signal-review-nav" aria-label="Items to check">
    <p>Nothing was saved · {itemsToCheck(refused)}</p>
    <Button variant="quiet" onClick={onShowRefusal}>Show these items</Button>
  </nav>;
  if (!items.length) return <nav className="signal-review-nav" aria-label="Items to check">
    <p>{unsaved ? `No items to check · ${unsaved} kept, not saved yet` : "No items to check"}</p>
    {unsaved > 0 && !readOnly && <Button variant="quiet" onClick={onSave}>Go to save</Button>}
  </nav>;
  const index = Math.max(0, items.findIndex((item) => item.id === currentId));
  const shown = currentId !== null && items.some((item) => item.id === currentId);
  const last = shown && index === items.length - 1;
  return <nav className="signal-review-nav" aria-label="Items to check">
    <p>{shown ? `Item ${index + 1} of ${items.length} to check` : itemsToCheck(items.length)}</p>
    <Button variant="quiet" disabled={shown && index === 0} onClick={() => onGo(items[shown ? index - 1 : items.length - 1])}>Previous item</Button>
    {last && !readOnly
      ? <Button variant="quiet" onClick={onSave}>Last item · go to save</Button>
      : <Button variant="quiet" disabled={last} onClick={() => onGo(items[shown ? index + 1 : 0])}>Next item</Button>}
  </nav>;
}

export function SourceReadingSummary({ summary }: { summary: RubricSourceSummary | null | undefined }) {
  if (!summary || (summary.copy_pages.length < 2 && summary.withheld_count === 0)) return null;
  const copies = summary.copy_pages.length;
  const pages = summary.copy_pages.join(", ");
  const left = summary.withheld_count;
  return <details className="signal-copy-flow signal-source-reading">
    <summary>{copies > 1 ? `Read once from ${copies} copies of the same table` : "Read from one table"}{left ? ` · ${left} ${left === 1 ? "entry" : "entries"} left out` : ""}</summary>
    <p>{copies > 1 ? `The same table appears on pages ${pages}. VERIDICAL read it once, from page ${summary.copy_pages[0]}. ` : ""}
      {left ? `${left} ${left === 1 ? "entry that differs" : "entries that differ"} between the copies, such as names written on a copy, ${left === 1 ? "was" : "were"} left out and ${left === 1 ? "is" : "are"} not part of any criterion. You can see them on those pages of the original.` : ""}</p>
  </details>;
}

function KeepWordingForm({ headingId, codes, consequence, initial, serverError, onKeep, onCancel }: {
  headingId: string;
  codes: readonly string[];
  consequence: string | null;
  // Set when the server refused this keep: the form opens again with what
  // was chosen, and says why at the note.
  initial?: { reason: RubricAcknowledgementReason; note: string | null };
  serverError?: string;
  onKeep: (reason: RubricAcknowledgementReason, note: string | null) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const reasons = keepReasonsFor(codes);
  const [reason, setReason] = useState<RubricAcknowledgementReason | null>(
    initial && reasons.some((option) => option.value === initial.reason) ? initial.reason : null);
  const [note, setNote] = useState(initial?.note ?? "");
  const [tried, setTried] = useState(Boolean(serverError));
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const keepEditingRef = useRef<HTMLButtonElement>(null);
  // A refused keep is focused by the page, which knows which one comes first.
  useEffect(() => { if (!serverError) firstRef.current?.focus(); }, [serverError]);
  useEffect(() => { if (confirmingCancel) keepEditingRef.current?.focus(); }, [confirmingCancel]);
  const noteRequired = keepNoteRequired(codes) || Boolean(serverError);
  const needsNote = noteRequired || reason === "other";
  const noteProblem = !needsNote ? null : !note.trim() ? "missing" : !noteHasContent(note) ? "short" : null;
  const missingReason = tried && reason === null;
  const noteError = tried && noteProblem
    ? serverError && noteProblem === "missing" ? serverError
      : noteProblem === "short"
        ? `Write at least ${RUBRIC_KEEP_NOTE_MIN_CHARS} letters or numbers ${noteRequired ? "saying what you compared" : "explaining the reason"}.`
        : noteRequired ? "Add a note saying what you compared." : "Add a note for this reason."
    : null;
  const touched = reason !== null || note.trim() !== "";

  function askBeforeCancel() {
    if (touched) setConfirmingCancel(true);
    else onCancel();
  }

  return <form className="signal-keep-form" aria-labelledby={headingId} data-refused={serverError ? "true" : undefined}
    onKeyDown={(event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      // Escape never throws away what was typed without asking.
      if (confirmingCancel) { setConfirmingCancel(false); (noteRef.current ?? firstRef.current)?.focus(); }
      else askBeforeCancel();
    }}
    onSubmit={(event) => {
      event.preventDefault();
      setTried(true);
      if (reason === null) { firstRef.current?.focus(); return; }
      if (noteProblem) { noteRef.current?.focus(); return; }
      onKeep(reason, note.trim() ? note.trim() : null);
    }}>
    <p id={`${id}-consequence`}>{consequence ? `${consequence} ` : ""}VERIDICAL will grade with this wording exactly as shown.</p>
    <fieldset aria-invalid={missingReason || undefined} aria-describedby={`${id}-consequence${missingReason ? ` ${id}-error` : ""}`}>
      <legend>Why keep it?</legend>
      {missingReason && <small id={`${id}-error`} className="signal-field-error">Choose why you are keeping this wording.</small>}
      {reasons.map((option, index) => <label key={option.value}>
        <input ref={index === 0 ? firstRef : undefined} type="radio" name={`${id}-reason`} value={option.value}
          checked={reason === option.value} onChange={() => setReason(option.value)} />
        {option.label}
      </label>)}
    </fieldset>
    <div className="signal-criterion-field">
      <label htmlFor={`${id}-note`}>{needsNote ? "Note (required)" : "Note (optional)"}</label>
      {noteRequired && <small id={`${id}-note-hint`}>Say what you compared in the original, for example where it has this wording.</small>}
      <textarea id={`${id}-note`} ref={noteRef} rows={2} maxLength={RUBRIC_KEEP_NOTE_MAX_LENGTH} value={note}
        aria-required={needsNote || undefined}
        aria-invalid={noteError ? true : undefined}
        aria-describedby={[noteRequired ? `${id}-note-hint` : "", noteError ? `${id}-note-error` : ""].filter(Boolean).join(" ") || undefined}
        onChange={(event) => setNote(event.target.value)} />
      {noteError && <small id={`${id}-note-error`} className="signal-field-error">{noteError}</small>}
    </div>
    {confirmingCancel
      ? <div className="signal-keep-form__actions" role="group" aria-labelledby={`${id}-discard`}>
          <p id={`${id}-discard`}>Discard what you entered?</p>
          <Button type="button" variant="danger" onClick={onCancel}>Discard</Button>
          <Button ref={keepEditingRef} type="button" variant="secondary"
            onClick={() => { setConfirmingCancel(false); (noteRef.current ?? firstRef.current)?.focus(); }}>Keep editing</Button>
        </div>
      : <div className="signal-keep-form__actions">
          <Button type="submit" variant="secondary">Keep wording</Button>
          <Button type="button" variant="quiet" onClick={askBeforeCancel}>Cancel</Button>
        </div>}
  </form>;
}

function IssueResolution({ status, readOnly, undoLabel, onUndo, pending, copyFor }: {
  status: LevelStatus;
  readOnly: boolean;
  undoLabel: string;
  onUndo: () => void;
  pending: ReadonlyMap<number, PendingAcknowledgement>;
  copyFor: (issue: RubricIssueDetail) => string;
}) {
  if (status.kind === "row_unchecked") return <p className="signal-level-row__note">Another level in this row did not match, so this one was not checked on its own.</p>;
  if (status.kind !== "check" && status.kind !== "kept_pending" && status.kind !== "kept") return null;
  // What VERIDICAL raised stays visible after the wording is kept, so the
  // record shows what the instructor decided about.
  const raised = <ul className={`signal-level-row__issues${status.kind === "check" ? "" : " is-settled"}`}>
    {status.issues.map((issue) => <li key={issue.id}>{copyFor(issue)}</li>)}
  </ul>;
  if (status.kind === "check") return raised;
  const first = status.issues[0];
  if (status.kind === "kept_pending") {
    const kept = pending.get(first.id);
    const reason = keepReasonLabel(kept?.reason_code, first.code);
    return <>{raised}<p className="signal-level-row__note">
      Kept as written{reason ? `: ${reason}` : ""}{kept?.note ? ` · “${kept.note}”` : ""}. Not saved yet.
      {!readOnly && <> <Button variant="quiet" aria-label={undoLabel} onClick={onUndo}>Undo</Button></>}
    </p></>;
  }
  const resolution = first?.resolution;
  const reason = keepReasonLabel(resolution?.reason_code, first?.code);
  return <>{raised}<p className="signal-level-row__note">
    {resolution?.by_name ? `Kept by ${resolution.by_name}` : "Kept"}{resolution ? `, ${shortDate(resolution.at)}` : ""}{reason ? `: ${reason}` : ""}
    {resolution?.note ? ` · “${resolution.note}”` : ""}
  </p></>;
}

/** One criterion's issues and levels. Row-level issues sit above the levels
 * because they are about how the levels fit together. */
export function CriterionLevels({
  criterionKey, criterionName, levels, issues, pending, readOnly, onKeep, onUndoKeep, onPageShown, nextAfter, onGo, onSave,
  noteRefused, summaryDescribedBy, forceOpen = false, rowHeading,
}: {
  criterionKey: string;
  criterionName: string;
  levels: readonly RubricLevel[];
  issues: readonly RubricIssueDetail[];
  pending: ReadonlyMap<number, PendingAcknowledgement>;
  readOnly: boolean;
  onKeep: (issues: readonly RubricIssueDetail[], reason: RubricAcknowledgementReason, note: string | null) => void;
  onUndoKeep: (issues: readonly RubricIssueDetail[]) => void;
  onPageShown: (page: number) => void;
  // The next item to check after these issues, for the per-item Next button.
  nextAfter: (issues: readonly RubricIssueDetail[]) => RubricIssueDetail | null;
  onGo: (issue: RubricIssueDetail) => void;
  onSave: () => void;
  // Issues whose keep the server refused for want of a note.
  noteRefused: ReadonlySet<number>;
  summaryDescribedBy?: string;
  forceOpen?: boolean;
  rowHeading?: string;
}) {
  const showPage = useShowSourcePage();
  const [keeping, setKeeping] = useState<number | "row" | null>(null);
  // An issue naming a level this criterion no longer has is about the row.
  const rowIssues = issues.filter((issue) => issue.level === null || !levels.some((level) => level.level === issue.level));
  const rowStatus = issueStatus(rowIssues, pending);
  const statuses = levels.map((level) => levelStatus(level, issues.filter((issue) => issue.level === level.level), pending));
  const refusedHere = issues.some((issue) => noteRefused.has(issue.id));
  const needsAttention = refusedHere || [...statuses, rowStatus].some((status) => status?.kind === "check" || status?.kind === "kept_pending");
  const allMatch = statuses.every((status) => status.kind === "matches") && rowStatus === null;
  // Counts the same levels the "Kept as written" tag marks.
  const keptSaved = statuses.filter((status) => status.kind === "kept").length;
  const keptUnsaved = statuses.filter((status) => status.kind === "kept_pending").length;
  // The row is where the levels that matched were found; a misplaced level
  // can point at another criterion's row.
  const matchedRow = rowLocation(levels.filter((_, index) => statuses[index].kind === "matches"));
  const row = matchedRow.text ? matchedRow : rowLocation(levels);
  const rowHeadingId = levelAnchorId(criterionKey, null);
  // Once open, the list stays open: settling the last item must not fold it
  // away from under the instructor.
  const [open, setOpen] = useState(needsAttention || forceOpen);
  useEffect(() => { if (needsAttention || forceOpen) setOpen(true); }, [needsAttention, forceOpen]);

  function focusLater(id: string) {
    requestAnimationFrame(() => document.getElementById(id)?.focus());
  }

  function showPageButton(page: number | null, placeName: string, target: SourceTarget) {
    if (!showPage || page === null) return null;
    return <Button variant="quiet" aria-label={`Show page ${page} of the original, ${criterionName}, ${placeName}`}
      onClick={(event) => { showPage(page, event.currentTarget, target); onPageShown(page); }}>Show page {page}</Button>;
  }

  // Wording found under another level's column: say which level's wording
  // this one would be graded with.
  function misplacedUnder(level: RubricLevel | undefined, targetIssues: readonly RubricIssueDetail[]) {
    if (!level || !targetIssues.some((issue) => issue.code === "wrong_column")) return null;
    return columnLevelName(level, levels);
  }

  function copyFor(level: RubricLevel | undefined) {
    return (issue: RubricIssueDetail) => {
      const under = issue.code === "wrong_column" && level ? columnLevelName(level, levels) : null;
      return under ? `This wording is in the document, but under ${under}, not ${level!.name}.` : issueCopy(issue);
    };
  }

  // After keeping, the per-item button moves on, or points to Save once
  // nothing is left to check.
  // "Purpose, Proficient" for a level; the criterion's name alone for its row.
  const placeLabel = (placeName: string | null) => placeName ? `${criterionName}, ${placeName}` : criterionName;

  function onwardButton(fromIssues: readonly RubricIssueDetail[], placeName: string | null) {
    const next = nextAfter(fromIssues);
    if (next) return <Button variant="quiet" aria-label={`Next item after ${placeLabel(placeName)}`} onClick={() => onGo(next)}>Next item</Button>;
    if (readOnly) return null;
    return <Button variant="quiet" aria-label={`Go to save, after ${placeLabel(placeName)}`} onClick={onSave}>Go to save</Button>;
  }

  function actions(target: number | "row", targetIssues: readonly RubricIssueDetail[], headingId: string, placeName: string | null, level?: RubricLevel) {
    if (!targetIssues.length) return null;
    const actionId = `${headingId}-keep`;
    const under = misplacedUnder(level, targetIssues);
    const consequence = under && level ? `Keeping it means ${level.name} is graded with the wording the document gives ${under}.` : null;
    const refused = targetIssues.filter((issue) => noteRefused.has(issue.id));
    if (refused.length && !readOnly) {
      const kept = pending.get(refused[0].id);
      return <KeepWordingForm headingId={headingId} codes={targetIssues.map((issue) => issue.code)} consequence={consequence}
        initial={kept ? { reason: kept.reason_code, note: kept.note } : undefined}
        serverError="Nothing was saved. Add a note saying what you compared before keeping this wording."
        onCancel={() => { onUndoKeep(targetIssues); focusLater(actionId); }}
        onKeep={(reason, note) => { onKeep(targetIssues, reason, note); focusLater(headingId); }} />;
    }
    if (keeping === target && !readOnly) return <KeepWordingForm headingId={headingId}
      codes={targetIssues.map((issue) => issue.code)} consequence={consequence}
      onCancel={() => { setKeeping(null); focusLater(actionId); }}
      onKeep={(reason, note) => { onKeep(targetIssues, reason, note); setKeeping(null); focusLater(headingId); }} />;
    const next = nextAfter(targetIssues);
    return <div className="signal-level-row__actions">
      {!readOnly && <Button id={actionId} variant="secondary" aria-label={`Keep this wording, ${placeLabel(placeName)}`} onClick={() => setKeeping(target)}>Keep this wording</Button>}
      {next && <Button variant="quiet" aria-label={`Next item after ${placeLabel(placeName)}`} onClick={() => onGo(next)}>Next item</Button>}
    </div>;
  }

  const rowBlock = rowStatus && <div className="signal-level-row signal-level-row--whole" data-status={rowStatus.kind}>
    <div className="signal-level-row__head">
      <h4 id={rowHeadingId} tabIndex={-1}><span className="sr-only">{criterionName}:</span> {rowHeading ?? (levels.length ? "How these levels fit the table" : "About this criterion")}</h4>
      <LevelStatusTag kind={rowStatus.kind} />
    </div>
    <IssueResolution status={rowStatus} readOnly={readOnly} pending={pending} undoLabel={`Undo keeping, ${criterionName}`} copyFor={copyFor(undefined)}
      onUndo={() => { onUndoKeep(rowIssues); focusLater(`${rowHeadingId}-keep`); }} />
    {row.text && levels.length > 0 && <p className="signal-level-row__location"><span>{row.text}</span>{showPageButton(row.page, "whole row", { location: row.text, text: null, kind: "row" })}</p>}
    {(rowStatus.kind === "check" || rowIssues.some((issue) => noteRefused.has(issue.id))) && actions("row", rowStatus.issues, rowHeadingId, null)}
    {rowStatus.kind === "kept_pending" && !rowIssues.some((issue) => noteRefused.has(issue.id)) && <div className="signal-level-row__actions">{onwardButton(rowStatus.issues, null)}</div>}
  </div>;
  if (!levels.length) return rowBlock ? <div className="signal-levels">{rowBlock}</div> : null;

  const keptParts = [
    keptSaved ? `${keptSaved} kept as written` : "",
    keptUnsaved ? `${keptUnsaved} kept as written, not saved yet` : "",
  ].filter(Boolean);
  return <details id={`criterion-levels-${criterionKey}`} className="signal-levels" open={open}
    onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary aria-describedby={summaryDescribedBy}>Levelled criterion · {levels.length} levels{allMatch ? " · all match the document" : ""}{keptParts.map((part) => ` · ${part}`).join("")}</summary>
    {rowBlock}
    <ol>{levels.map((level, index) => {
      const status = statuses[index];
      const headingId = levelAnchorId(criterionKey, level.level);
      const ownText = level.source && level.source.match !== "unmatched" ? sourceLocation(level.source) : null;
      const own = level.source && ownText ? { page: level.source.page, text: ownText } : null;
      // A level that could not be placed still points at its criterion's row.
      const location = own ?? (row.text ? row : null);
      // "Show page" says what to look for: the cell's own text where the
      // level was found, otherwise the wording to compare.
      const target: SourceTarget | null = location?.text
        ? own && level.source?.cell_text
          ? { location: location.text, text: level.source.cell_text, kind: "cell" }
          : { location: location.text, text: level.descriptor, kind: "compare" }
        : null;
      const levelIssues = status.kind === "check" || status.kind === "kept_pending" || status.kind === "kept" ? status.issues : [];
      const refused = levelIssues.some((issue) => noteRefused.has(issue.id));
      return <li key={level.level} className="signal-level-row" data-status={status.kind}>
        <div className="signal-level-row__head">
          <h4 id={headingId} tabIndex={-1}><span className="sr-only">{criterionName},</span> {level.name}</h4>
          <LevelStatusTag kind={status.kind} />
        </div>
        <p className="signal-level-row__descriptor">{level.descriptor}</p>
        <IssueResolution status={status} readOnly={readOnly} pending={pending} undoLabel={`Undo keeping, ${criterionName}, ${level.name}`}
          copyFor={copyFor(level)} onUndo={() => { onUndoKeep(levelIssues); focusLater(`${headingId}-keep`); }} />
        {location?.text && target && <p className="signal-level-row__location">
          <span>{location.text}</span>{showPageButton(location.page, level.name, target)}
        </p>}
        {(status.kind === "check" || refused) && actions(level.level, levelIssues, headingId, level.name, level)}
        {status.kind === "kept_pending" && !refused && <div className="signal-level-row__actions">{onwardButton(status.issues, level.name)}</div>}
      </li>;
    })}</ol>
  </details>;
}
