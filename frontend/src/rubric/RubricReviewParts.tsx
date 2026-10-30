// BUG-566 (screen 4d): the instructor checks each level description against
// the uploaded format before the format can grade. Matched levels stay
// quiet; only the ones VERIDICAL could not match ask for a decision.
import { useId, useRef, useState, type ReactNode } from "react";
import type { RubricAcknowledgementReason, RubricIssueDetail, RubricLevel, RubricSourceSummary } from "../api/types";
import { RUBRIC_KEEP_NOTE_MAX_LENGTH, RUBRIC_KEEP_REASONS } from "../config/ui";
import { Button } from "../ui/Button";
import { useShowSourcePage } from "./sourcePage";
import {
  issueCopy, issueStatus, levelAnchorId, levelStatus, shortDate, sourceLocation, tidy,
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
  not_checked: { label: "Not checked against the document", icon: DashIcon },
};

export function LevelStatusTag({ kind }: { kind: LevelStatus["kind"] }) {
  const tag = TAGS[kind];
  return <span className={`signal-level-tag signal-level-tag--${kind.replace("_", "-")}`}><tag.icon />{tag.label}</span>;
}

export function RubricReviewStatus({ total, open, readOnly, isActive, legacy, onFirst }: {
  total: number;
  open: number;
  readOnly: boolean;
  isActive: boolean;
  legacy: boolean;
  onFirst: () => void;
}) {
  if (legacy) return <div className="signal-review-status" role="status">
    <p>This version was prepared before VERIDICAL compared level descriptions with the document. Its level descriptions have not been checked.</p>
  </div>;
  if (readOnly) return <div className={`signal-review-status${open ? " is-open" : ""}`} role="status">
    <p>{open
      ? `${open} ${open === 1 ? "item" : "items"} in this version could not be matched with the document. This version is locked, so corrections go into a new version.`
      : total ? "Every item VERIDICAL raised in this version was checked." : "Every level description matches a cell in the document."}</p>
    {open > 0 && <Button variant="secondary" onClick={onFirst}>Go to first item</Button>}
  </div>;
  return <div className={`signal-review-status${open ? " is-open" : ""}`} role="status">
    <p>{open
      ? isActive
        ? `${open} ${open === 1 ? "item" : "items"} to check in this active format`
        : `${open} ${open === 1 ? "item" : "items"} to check before this format can be activated`
      : total
        ? `All ${total} ${total === 1 ? "item is" : "items are"} checked.${isActive ? "" : " You can activate this format."}`
        : "Every level description matches a cell in the document. Read each criterion before you activate."}</p>
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
      {left ? `${left} ${left === 1 ? "entry that differs" : "entries that differ"} between the copies, such as names written on a copy, ${left === 1 ? "was" : "were"} left out and ${left === 1 ? "is" : "are"} not part of any criterion. They are not shown here.` : ""}</p>
  </details>;
}

function KeepWordingForm({ headingId, onKeep, onCancel }: {
  headingId: string;
  onKeep: (reason: RubricAcknowledgementReason, note: string | null) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState<RubricAcknowledgementReason | null>(null);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);
  const missing = tried && reason === null;
  return <form className="signal-keep-form" aria-labelledby={headingId} onSubmit={(event) => {
    event.preventDefault();
    setTried(true);
    if (reason === null) { firstRef.current?.focus(); return; }
    onKeep(reason, note.trim() ? note.trim() : null);
  }}>
    <fieldset aria-invalid={missing || undefined} aria-describedby={missing ? `${id}-error` : undefined}>
      <legend>Why keep it?</legend>
      {missing && <small id={`${id}-error`} className="signal-field-error">Choose why you are keeping this wording.</small>}
      {RUBRIC_KEEP_REASONS.map((option, index) => <label key={option.value}>
        <input ref={index === 0 ? firstRef : undefined} type="radio" name={`${id}-reason`} value={option.value}
          checked={reason === option.value} onChange={() => setReason(option.value)} autoFocus={index === 0} />
        {option.label}
      </label>)}
    </fieldset>
    <label className="signal-criterion-field">
      <span>Note (optional)</span>
      <textarea rows={2} maxLength={RUBRIC_KEEP_NOTE_MAX_LENGTH} value={note} onChange={(event) => setNote(event.target.value)} />
    </label>
    <div className="signal-keep-form__actions">
      <Button type="submit" variant="secondary">Keep wording</Button>
      <Button type="button" variant="quiet" onClick={onCancel}>Cancel</Button>
    </div>
  </form>;
}

function IssueResolution({ status, readOnly, onUndo }: { status: LevelStatus; readOnly: boolean; onUndo: () => void }) {
  if (status.kind === "check") return status.issues.length
    ? <ul className="signal-level-row__issues">{status.issues.map((issue) => <li key={issue.id}>{issueCopy(issue)}</li>)}</ul>
    : <p className="signal-level-row__issues">VERIDICAL could not match this wording with the document. Compare it with the original.</p>;
  if (status.kind === "kept_pending") return <p className="signal-level-row__note">
    Not saved yet{!readOnly && <> · <Button variant="quiet" onClick={onUndo}>Undo</Button></>}
  </p>;
  if (status.kind === "kept") {
    const resolution = status.issues[0]?.resolution;
    const by = resolution?.by_name ? `Checked by ${resolution.by_name}` : "Checked";
    return <p className="signal-level-row__note">{resolution ? `${by}, ${shortDate(resolution.at)}` : "Checked"}{resolution?.note ? ` · “${resolution.note}”` : ""}</p>;
  }
  return null;
}

/** One criterion's issues and levels. Row-level issues sit above the levels
 * because they are about how the levels fit together. */
export function CriterionLevels({ criterionKey, criterionLabel, levels, issues, pending, readOnly, onKeep, onUndoKeep, onPageShown, summaryDescribedBy, forceOpen = false }: {
  criterionKey: string;
  criterionLabel: string;
  levels: readonly RubricLevel[];
  issues: readonly RubricIssueDetail[];
  pending: ReadonlyMap<number, PendingAcknowledgement>;
  readOnly: boolean;
  onKeep: (issues: readonly RubricIssueDetail[], reason: RubricAcknowledgementReason, note: string | null) => void;
  onUndoKeep: (issues: readonly RubricIssueDetail[]) => void;
  onPageShown: (page: number) => void;
  summaryDescribedBy?: string;
  forceOpen?: boolean;
}) {
  const showPage = useShowSourcePage();
  const [keeping, setKeeping] = useState<number | "row" | null>(null);
  // An issue naming a level this criterion no longer has is about the row.
  const rowIssues = issues.filter((issue) => issue.level === null || !levels.some((level) => level.level === issue.level));
  const rowStatus = issueStatus(rowIssues, pending);
  const statuses = levels.map((level) => levelStatus(level, issues.filter((issue) => issue.level === level.level), pending));
  const needsAttention = [...statuses, rowStatus].some((status) => status?.kind === "check" || status?.kind === "kept_pending");
  const allMatch = statuses.every((status) => status.kind === "matches") && rowStatus === null;
  const rowHeadingId = levelAnchorId(criterionKey, null);

  function focusLater(id: string) {
    requestAnimationFrame(() => document.getElementById(id)?.focus());
  }

  function keepActions(target: number | "row", targetIssues: readonly RubricIssueDetail[], headingId: string, actionId: string) {
    if (readOnly || !targetIssues.length) return null;
    return keeping === target
      ? <KeepWordingForm headingId={headingId}
          onCancel={() => { setKeeping(null); focusLater(actionId); }}
          onKeep={(reason, note) => { onKeep(targetIssues, reason, note); setKeeping(null); focusLater(headingId); }} />
      : <Button id={actionId} variant="secondary" onClick={() => setKeeping(target)}>Keep this wording</Button>;
  }

  const rowBlock = rowStatus && <div className="signal-level-row signal-level-row--whole" data-status={rowStatus.kind}>
      <div className="signal-level-row__head">
        <h4 id={rowHeadingId} tabIndex={-1}>{levels.length ? "How these levels fit the table" : "Checked against the document"}</h4>
        <LevelStatusTag kind={rowStatus.kind} />
      </div>
      <IssueResolution status={rowStatus} readOnly={readOnly} onUndo={() => { onUndoKeep(rowIssues); focusLater(`${rowHeadingId}-keep`); }} />
      {rowStatus.kind === "check" && keepActions("row", rowStatus.issues, rowHeadingId, `${rowHeadingId}-keep`)}
    </div>;
  if (!levels.length) return rowBlock ? <div className="signal-levels">{rowBlock}</div> : null;

  return <details id={`criterion-levels-${criterionKey}`} className="signal-levels" open={needsAttention || forceOpen || undefined}>
    <summary aria-describedby={summaryDescribedBy}>Levelled criterion · {levels.length} levels{allMatch ? " · all match the document" : ""}</summary>
    {rowBlock}
    <ol>{levels.map((level, index) => {
      const status = statuses[index];
      const headingId = levelAnchorId(criterionKey, level.level);
      const location = level.source ? sourceLocation(level.source) : null;
      const page = level.source?.page ?? null;
      const levelIssues = status.kind === "check" || status.kind === "kept_pending" || status.kind === "kept" ? status.issues : [];
      return <li key={level.level} className="signal-level-row" data-status={status.kind}>
        <div className="signal-level-row__head">
          <h4 id={headingId} tabIndex={-1}>{level.name}</h4>
          <LevelStatusTag kind={status.kind} />
        </div>
        {level.source?.column_heading && <p className="signal-level-row__column">Document column: {tidy(level.source.column_heading)}</p>}
        <p className="signal-level-row__descriptor">{level.descriptor}</p>
        <IssueResolution status={status} readOnly={readOnly} onUndo={() => { onUndoKeep(levelIssues); focusLater(`${headingId}-keep`); }} />
        {location && <p className="signal-level-row__location">
          <span>{location}</span>
          {showPage && page !== null && <Button variant="quiet" aria-label={`Show page ${page} of the original, ${criterionLabel}, ${level.name}`}
            onClick={(event) => { showPage(page, event.currentTarget); onPageShown(page); }}>Show page {page}</Button>}
        </p>}
        {status.kind === "check" && keepActions(level.level, status.issues, headingId, `${headingId}-keep`)}
      </li>;
    })}</ol>
  </details>;
}
