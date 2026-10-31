// BUG-566 (screen 4d): the instructor checks each level description against
// the uploaded format before the format can grade. Matched levels stay
// quiet; only the ones VERIDICAL could not match ask for a decision.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { RubricAcknowledgementReason, RubricIssueDetail, RubricLevel, RubricSourceSummary } from "../api/types";
import { RUBRIC_KEEP_NOTE_MAX_LENGTH, RUBRIC_KEEP_NOTE_REQUIRED_CODES, RUBRIC_KEEP_REASONS } from "../config/ui";
import { Button } from "../ui/Button";
import { useShowSourcePage } from "./sourcePage";
import {
  issueCopy, issueStatus, keepReasonLabel, levelAnchorId, levelStatus, rowLocation, shortDate, sourceLocation,
  type LevelStatus, type PendingAcknowledgement,
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

export function RubricReviewStatus({ total, open, readOnly, isActive, legacy, onFirst }: {
  total: number;
  open: number;
  readOnly: boolean;
  isActive: boolean;
  legacy: boolean;
  onFirst: () => void;
}) {
  const items = (count: number) => `${count} ${count === 1 ? "item" : "items"}`;
  if (legacy) return <div className="signal-review-status" role="status">
    <p>This version was prepared before VERIDICAL compared level descriptions with the document. Its level descriptions have not been checked.</p>
  </div>;
  const text = readOnly
    ? open
      ? `${items(open)} in this version could not be matched with the document. This version is locked, so corrections go into a new version.`
      : total ? "Every item VERIDICAL raised in this version was kept or settled." : "Every level description VERIDICAL could place matches a cell in the document."
    : open
      ? isActive
        ? `${items(open)} to check in this active format. Changes can be saved once every item is kept.`
        : `${items(open)} to check before this format can be activated`
      : total
        ? `All ${items(total)} kept or settled.${isActive ? "" : " You can activate this format."}`
        : "Every level description VERIDICAL could place matches a cell in the document. Read each criterion before you activate.";
  return <div className={`signal-review-status${open ? " is-open" : ""}`} role="status">
    <div className="signal-copy-flow">
      <p>{text}</p>
      {open > 0 && !readOnly && <p className="signal-review-status__hint">
        You cannot correct a level's wording on this page yet. For now, keep the wording with a note, remove the criterion,
        {isActive ? " or upload a corrected copy of the format as a new version." : " or leave this format inactive and upload a corrected copy."}
      </p>}
    </div>
    {open > 0 && <Button variant="secondary" onClick={onFirst}>Go to first item</Button>}
  </div>;
}

export function ReviewItemNavigator({ items, currentId, onGo }: {
  items: readonly RubricIssueDetail[];
  currentId: number | null;
  onGo: (issue: RubricIssueDetail) => void;
}) {
  if (!items.length) return <nav className="signal-review-nav" aria-label="Items to check"><p>No items left to check</p></nav>;
  const index = Math.max(0, items.findIndex((item) => item.id === currentId));
  const shown = currentId !== null && items.some((item) => item.id === currentId);
  return <nav className="signal-review-nav" aria-label="Items to check">
    <p>{shown ? `Item ${index + 1} of ${items.length}` : `${items.length} ${items.length === 1 ? "item" : "items"} to check`}</p>
    <Button variant="quiet" disabled={shown && index === 0} onClick={() => onGo(items[shown ? index - 1 : items.length - 1])}>Previous item</Button>
    <Button variant="quiet" disabled={shown && index === items.length - 1} onClick={() => onGo(items[shown ? index + 1 : 0])}>Next item</Button>
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

function KeepWordingForm({ headingId, noteRequired, onKeep, onCancel }: {
  headingId: string;
  noteRequired: boolean;
  onKeep: (reason: RubricAcknowledgementReason, note: string | null) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState<RubricAcknowledgementReason | null>(null);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { firstRef.current?.focus(); }, []);
  const missingReason = tried && reason === null;
  const needsNote = noteRequired || reason === "other";
  const missingNote = tried && needsNote && !note.trim();
  return <form className="signal-keep-form" aria-labelledby={headingId}
    onKeyDown={(event) => {
      // Escape closes an untouched form; once something is entered, Cancel does.
      if (event.key === "Escape" && reason === null && !note.trim()) { event.preventDefault(); onCancel(); }
    }}
    onSubmit={(event) => {
      event.preventDefault();
      setTried(true);
      if (reason === null) { firstRef.current?.focus(); return; }
      if (needsNote && !note.trim()) { noteRef.current?.focus(); return; }
      onKeep(reason, note.trim() ? note.trim() : null);
    }}>
    <p id={`${id}-consequence`}>VERIDICAL will grade with this wording exactly as shown.</p>
    <fieldset aria-invalid={missingReason || undefined} aria-describedby={`${id}-consequence${missingReason ? ` ${id}-error` : ""}`}>
      <legend>Why keep it?</legend>
      {missingReason && <small id={`${id}-error`} className="signal-field-error">Choose why you are keeping this wording.</small>}
      {RUBRIC_KEEP_REASONS.map((option, index) => <label key={option.value}>
        <input ref={index === 0 ? firstRef : undefined} type="radio" name={`${id}-reason`} value={option.value}
          checked={reason === option.value} onChange={() => setReason(option.value)} />
        {option.label}
      </label>)}
    </fieldset>
    <div className="signal-criterion-field">
      <label htmlFor={`${id}-note`}>{needsNote ? "Note" : "Note (optional)"}</label>
      {noteRequired && <small id={`${id}-note-hint`}>Say what you compared in the original, for example where it has this wording.</small>}
      <textarea id={`${id}-note`} ref={noteRef} rows={2} maxLength={RUBRIC_KEEP_NOTE_MAX_LENGTH} value={note}
        aria-invalid={missingNote || undefined}
        aria-describedby={[noteRequired ? `${id}-note-hint` : "", missingNote ? `${id}-note-error` : ""].filter(Boolean).join(" ") || undefined}
        onChange={(event) => setNote(event.target.value)} />
      {missingNote && <small id={`${id}-note-error`} className="signal-field-error">{noteRequired ? "Add a note saying what you compared." : "Add a note for this reason."}</small>}
    </div>
    <div className="signal-keep-form__actions">
      <Button type="submit" variant="secondary">Keep wording</Button>
      <Button type="button" variant="quiet" onClick={onCancel}>Cancel</Button>
    </div>
  </form>;
}

function IssueResolution({ status, readOnly, undoLabel, onUndo, pending }: {
  status: LevelStatus;
  readOnly: boolean;
  undoLabel: string;
  onUndo: () => void;
  pending: ReadonlyMap<number, PendingAcknowledgement>;
}) {
  if (status.kind === "row_unchecked") return <p className="signal-level-row__note">Another level in this row did not match, so this one was not checked on its own.</p>;
  if (status.kind !== "check" && status.kind !== "kept_pending" && status.kind !== "kept") return null;
  // What VERIDICAL raised stays visible after the wording is kept, so the
  // record shows what the instructor decided about.
  const raised = <ul className={`signal-level-row__issues${status.kind === "check" ? "" : " is-settled"}`}>
    {status.issues.map((issue) => <li key={issue.id}>{issueCopy(issue)}</li>)}
  </ul>;
  if (status.kind === "check") return raised;
  if (status.kind === "kept_pending") {
    const reason = keepReasonLabel(pending.get(status.issues[0].id)?.reason_code);
    return <>{raised}<p className="signal-level-row__note">
      Kept as written{reason ? `: ${reason}` : ""}. Not saved yet.
      {!readOnly && <> <Button variant="quiet" aria-label={undoLabel} onClick={onUndo}>Undo</Button></>}
    </p></>;
  }
  const resolution = status.issues[0]?.resolution;
  const reason = keepReasonLabel(resolution?.reason_code);
  return <>{raised}<p className="signal-level-row__note">
    {resolution?.by_name ? `Kept by ${resolution.by_name}` : "Kept"}{resolution ? `, ${shortDate(resolution.at)}` : ""}{reason ? `: ${reason}` : ""}
    {resolution?.note ? ` · “${resolution.note}”` : ""}
  </p></>;
}

/** One criterion's issues and levels. Row-level issues sit above the levels
 * because they are about how the levels fit together. */
export function CriterionLevels({
  criterionKey, criterionName, levels, issues, pending, readOnly, onKeep, onUndoKeep, onPageShown, nextAfter, onGo,
  summaryDescribedBy, forceOpen = false, rowHeading,
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
  const needsAttention = [...statuses, rowStatus].some((status) => status?.kind === "check" || status?.kind === "kept_pending");
  const allMatch = statuses.every((status) => status.kind === "matches") && rowStatus === null;
  // Counts the same levels the "Kept as written" tag marks, saved or not.
  const kept = statuses.filter((status) => status.kind === "kept" || status.kind === "kept_pending").length;
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

  function showPageButton(page: number | null, levelName: string) {
    if (!showPage || page === null) return null;
    return <Button variant="quiet" aria-label={`Show page ${page} of the original, ${criterionName}, ${levelName}`}
      onClick={(event) => { showPage(page, event.currentTarget); onPageShown(page); }}>Show page {page}</Button>;
  }

  function nextButton(fromIssues: readonly RubricIssueDetail[]) {
    const next = nextAfter(fromIssues);
    return next ? <div className="signal-level-row__actions"><Button variant="quiet" onClick={() => onGo(next)}>Next item</Button></div> : null;
  }

  function actions(target: number | "row", targetIssues: readonly RubricIssueDetail[], headingId: string, label: string) {
    if (!targetIssues.length) return null;
    const actionId = `${headingId}-keep`;
    const next = nextAfter(targetIssues);
    if (keeping === target && !readOnly) return <KeepWordingForm headingId={headingId}
      noteRequired={targetIssues.some((issue) => RUBRIC_KEEP_NOTE_REQUIRED_CODES.includes(issue.code))}
      onCancel={() => { setKeeping(null); focusLater(actionId); }}
      onKeep={(reason, note) => { onKeep(targetIssues, reason, note); setKeeping(null); focusLater(headingId); }} />;
    return <div className="signal-level-row__actions">
      {!readOnly && <Button id={actionId} variant="secondary" aria-label={`Keep this wording, ${label}`} onClick={() => setKeeping(target)}>Keep this wording</Button>}
      {next && <Button variant="quiet" onClick={() => onGo(next)}>Next item</Button>}
    </div>;
  }

  const rowBlock = rowStatus && <div className="signal-level-row signal-level-row--whole" data-status={rowStatus.kind}>
    <div className="signal-level-row__head">
      <h4 id={rowHeadingId} tabIndex={-1}>{rowHeading ?? (levels.length ? "How these levels fit the table" : "About this criterion")}</h4>
      <LevelStatusTag kind={rowStatus.kind} />
    </div>
    <IssueResolution status={rowStatus} readOnly={readOnly} pending={pending} undoLabel={`Undo keeping, ${criterionName}`}
      onUndo={() => { onUndoKeep(rowIssues); focusLater(`${rowHeadingId}-keep`); }} />
    {row.text && levels.length > 0 && <p className="signal-level-row__location"><span>{row.text}</span>{showPageButton(row.page, "whole row")}</p>}
    {rowStatus.kind === "check" && actions("row", rowStatus.issues, rowHeadingId, criterionName)}
    {rowStatus.kind === "kept_pending" && nextButton(rowStatus.issues)}
  </div>;
  if (!levels.length) return rowBlock ? <div className="signal-levels">{rowBlock}</div> : null;

  return <details id={`criterion-levels-${criterionKey}`} className="signal-levels" open={open}
    onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary aria-describedby={summaryDescribedBy}>Levelled criterion · {levels.length} levels{allMatch ? " · all match the document" : ""}{kept ? ` · ${kept} kept as written` : ""}</summary>
    {rowBlock}
    <ol>{levels.map((level, index) => {
      const status = statuses[index];
      const headingId = levelAnchorId(criterionKey, level.level);
      const ownText = level.source && level.source.match !== "unmatched" ? sourceLocation(level.source) : null;
      const own = level.source && ownText ? { page: level.source.page, text: ownText } : null;
      // A level that could not be placed still points at its criterion's row.
      const location = own ?? (row.text ? row : null);
      const levelIssues = status.kind === "check" || status.kind === "kept_pending" || status.kind === "kept" ? status.issues : [];
      return <li key={level.level} className="signal-level-row" data-status={status.kind}>
        <div className="signal-level-row__head">
          <h4 id={headingId} tabIndex={-1}>{level.name}</h4>
          <LevelStatusTag kind={status.kind} />
        </div>
        <p className="signal-level-row__descriptor">{level.descriptor}</p>
        <IssueResolution status={status} readOnly={readOnly} pending={pending} undoLabel={`Undo keeping, ${criterionName}, ${level.name}`}
          onUndo={() => { onUndoKeep(levelIssues); focusLater(`${headingId}-keep`); }} />
        {location?.text && <p className="signal-level-row__location">
          <span>{location.text}</span>{showPageButton(location.page, level.name)}
        </p>}
        {status.kind === "check" && actions(level.level, status.issues, headingId, `${criterionName}, ${level.name}`)}
        {status.kind === "kept_pending" && nextButton(status.issues)}
      </li>;
    })}</ol>
  </details>;
}
