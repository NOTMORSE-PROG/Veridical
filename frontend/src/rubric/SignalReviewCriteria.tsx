import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { CriterionType, RubricAcknowledgementReason, RubricIssueDetail, RubricLevel } from "../api/types";
import { RUBRIC_CRITERION_NAME_MAX_CHARS, WEIGHT_IMPORTANCE_PREVIEW } from "../config/ui";
import { useRouteFocus } from "../routing/useRouteFocus";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { type CriterionEdit, useRubric, useSaveCriteria } from "./useRubric";
import { UploadRubricModal } from "./UploadRubricModal";
import { FormatReviewWorkbench } from "./FormatReviewWorkbench";
import { equalWeights, readableWeights } from "./weights";
import { CriterionLevels, ReviewItemNavigator, RubricReviewStatus, SourceReadingSummary } from "./RubricReviewParts";
import {
  formatReviewState, levelAnchorId, levelIssueCopy, openIssues, orderedIssues, tidy, type OpenedAgain, type PendingAcknowledgement,
} from "./rubricReview";

interface CriterionRow {
  key: string;
  id: number | null;
  type: CriterionType;
  text: string;
  evidence: string;
  weight: number;
  levels: RubricLevel[] | null;
}

const TYPE_LABELS: Record<CriterionType, string> = {
  structural: "Structural",
  semantic: "Semantic",
  not_assessable: "Not from the document",
};

let localCriterionId = 0;

function invalidWeight(weight: number): boolean {
  return !Number.isFinite(weight) || weight <= 0;
}

function invalidCriteria(rows: CriterionRow[]): boolean {
  return rows.length === 0 || rows.some((row) => !row.text.trim() || invalidWeight(row.weight));
}

function importance(weight: number, average: number): "Low" | "Medium" | "High" {
  if (average <= 0) return "Medium";
  const ratio = weight / average;
  if (ratio < WEIGHT_IMPORTANCE_PREVIEW.lowMaxRatio) return "Low";
  if (ratio >= WEIGHT_IMPORTANCE_PREVIEW.highMinRatio) return "High";
  return "Medium";
}

// A refused save names the field it refused ("criteria.2.text"). Each such
// message is shown under that criterion's input, keyed by the row that was
// sent at that position.
type ServerField = "text" | "evidence" | "levels";
type ServerFieldErrors = Map<string, Partial<Record<ServerField, string>>>;

function unchangedRefusals(errors: Partial<Record<ServerField, string>> | undefined, key: string, edited: ReadonlySet<string>) {
  if (!errors) return undefined;
  const kept = Object.fromEntries(Object.entries(errors).filter(([field]) => !edited.has(`${key}.${field}`))) as Partial<Record<ServerField, string>>;
  return Object.keys(kept).length ? kept : undefined;
}

const LEVEL_PART: Record<string, string> = { name: "name", descriptor: "description" };
// Levels come from the uploaded format and are not edited on this screen.
const LEVELS_HINT = "Levels come from the uploaded format, so correct it there and upload the format again.";

const OTHER_FIELD_LABEL: Record<string, string> = { weight: "Relative weight", type: "Type" };

function serverFieldErrors(error: unknown, submittedKeys: readonly string[]): { byRow: ServerFieldErrors; other: string[]; count: number } {
  const byRow: ServerFieldErrors = new Map();
  const levels = new Map<string, string[]>();
  const other: string[] = [];
  let count = 0;
  if (!(error instanceof ApiError) || error.code !== "validation_error") return { byRow, other, count };
  for (const [field, message] of error.fieldMessages) {
    count += 1;
    const match = /^criteria\.(\d+)\.(text|evidence|levels)(?:\.(\d+)\.(name|descriptor))?\b/.exec(field);
    const key = match ? submittedKeys[Number(match[1])] : undefined;
    // A refusal this editor cannot place on a field is still listed, never
    // dropped, with its criterion and field when the server named them.
    if (!match || !key) {
      const place = /^criteria\.(\d+)\.(\w+)/.exec(field);
      other.push(place ? `Criterion ${Number(place[1]) + 1}, ${OTHER_FIELD_LABEL[place[2]] ?? place[2]}: ${message}` : message);
      continue;
    }
    const name = match[2] as ServerField;
    if (name === "levels") {
      // Each refused level keeps its own reason.
      const part = match[3] !== undefined ? `Level ${Number(match[3]) + 1}${match[4] ? ` ${LEVEL_PART[match[4]]}` : ""}: ` : "";
      levels.set(key, [...(levels.get(key) ?? []), `${part}${message}`]);
      byRow.set(key, { ...byRow.get(key), levels: `${levels.get(key)!.join(" ")} ${LEVELS_HINT}` });
    } else if (!byRow.get(key)?.[name]) byRow.set(key, { ...byRow.get(key), [name]: message });
  }
  return { byRow, other, count };
}

const SERVER_FIELD_LABEL: Record<ServerField, string> = { text: "Criterion text", evidence: "Evidence expected", levels: "Levels" };
const SERVER_FIELD_ID: Record<ServerField, string> = { text: "criterion-text", evidence: "criterion-evidence", levels: "criterion-levels" };

// Takes the instructor to a refused field: the editor is long, and the
// summary sits far from most criteria.
function focusRefusedField(key: string, field: ServerField) {
  const target = document.getElementById(`${SERVER_FIELD_ID[field]}-${key}`);
  if (!target) return;
  if (target instanceof HTMLDetailsElement) target.open = true;
  target.scrollIntoView({ block: "center" });
  (target instanceof HTMLDetailsElement ? target.querySelector("summary") ?? target : target).focus({ preventScroll: true });
}

function toEdits(rows: CriterionRow[]): CriterionEdit[] {
  return rows.map((row) => ({
    id: row.id,
    type: row.type,
    text: row.text,
    evidence: row.evidence.trim() ? row.evidence : null,
    weight: row.weight,
    levels: row.levels,
  }));
}

function CriteriaHeader({ headingRef, title, count, readOnly = false, familyId, version, instruction }: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  title?: string;
  count?: number;
  readOnly?: boolean;
  familyId?: string;
  version?: number;
  instruction?: string;
}) {
  return (
    <header className="signal-route-header signal-criteria-header">
      <div>
        <nav aria-label="Breadcrumb"><Link to={familyId ? `/rubric?family=${encodeURIComponent(familyId)}` : "/rubric"}>Required format</Link><span aria-hidden="true">/</span><span>Criteria review</span></nav>
        <h1 ref={headingRef} tabIndex={-1}>{readOnly ? "View criteria" : "Review prepared criteria"}</h1>
        {title && <p className="signal-route-header__intro">{title}{version !== undefined ? ` · Version ${version}` : ""}{count !== undefined ? ` · ${count} ${count === 1 ? "criterion" : "criteria"}` : ""}</p>}
        {instruction && <p>{instruction}</p>}
      </div>
    </header>
  );
}

interface LevelReview {
  name: string;
  // Levels with the server's latest placement; every save rechecks them.
  levels: readonly RubricLevel[];
  issues: readonly RubricIssueDetail[];
  nextAfter: (issues: readonly RubricIssueDetail[]) => RubricIssueDetail | null;
  onGo: (issue: RubricIssueDetail) => void;
  pending: ReadonlyMap<number, PendingAcknowledgement>;
  onKeep: (issues: readonly RubricIssueDetail[], reason: RubricAcknowledgementReason, note: string | null) => void;
  onUndoKeep: (issues: readonly RubricIssueDetail[]) => void;
  onPageShown: (page: number) => void;
  onSave: () => void;
  noteRefused: ReadonlySet<number>;
  openAgain: ReadonlyMap<number, OpenedAgain>;
}

const EDIT_FIELD_LABEL = { text: "criterion text", type: "type", evidence: "evidence expected", weight: "relative weight" } as const;

function shortName(text: string, fallback: string): string {
  const name = tidy(text);
  if (!name) return fallback;
  return name.length > RUBRIC_CRITERION_NAME_MAX_CHARS ? `${name.slice(0, RUBRIC_CRITERION_NAME_MAX_CHARS).trimEnd()}…` : name;
}

/** The edits a save would have made, in words: "Introduction: criterion
 * text changed", "Purpose: removed". A refused save names them so the
 * instructor knows what to change back. */
function describeEdits(submitted: readonly CriterionRow[], saved: readonly CriterionRow[]): string[] {
  const out: string[] = [];
  submitted.forEach((row, index) => {
    const name = shortName(row.text, `Criterion ${index + 1}`);
    const before = row.id === null ? undefined : saved.find((item) => item.id === row.id);
    if (!before) { out.push(`${name}: added`); return; }
    const changed = (Object.keys(EDIT_FIELD_LABEL) as (keyof typeof EDIT_FIELD_LABEL)[])
      .filter((field) => before[field] !== row[field]).map((field) => EDIT_FIELD_LABEL[field]);
    if (changed.length) out.push(`${shortName(before.text, name)}: ${changed.join(", ")} changed`);
  });
  saved.forEach((row, index) => {
    if (row.id !== null && !submitted.some((item) => item.id === row.id)) out.push(`${shortName(row.text, `Criterion ${index + 1}`)}: removed`);
  });
  return out;
}

function CriterionCard({
  row,
  index,
  average,
  allEqual,
  readOnly,
  showErrors,
  onChange,
  onRemove,
  serverErrors,
  review,
}: {
  row: CriterionRow;
  index: number;
  average: number;
  // Every criterion has the same weight; the count of criteria.
  allEqual: number | null;
  readOnly: boolean;
  showErrors: boolean;
  onChange: (patch: Partial<CriterionRow>) => void;
  onRemove: () => void;
  serverErrors?: Partial<Record<ServerField, string>>;
  review: LevelReview;
}) {
  const levels = (review.levels.length || review.issues.length) ? <CriterionLevels criterionKey={row.key} criterionName={review.name}
    levels={review.levels} issues={review.issues} pending={review.pending} readOnly={readOnly}
    onKeep={review.onKeep} onUndoKeep={review.onUndoKeep} onPageShown={review.onPageShown} nextAfter={review.nextAfter} onGo={review.onGo}
    onSave={review.onSave} noteRefused={review.noteRefused} openAgain={review.openAgain}
    summaryDescribedBy={serverErrors?.levels ? `criterion-levels-server-${row.key}` : undefined} forceOpen={Boolean(serverErrors?.levels)} /> : null;
  const textError = showErrors && row.text.trim() === "";
  const weightError = showErrors && invalidWeight(row.weight);
  if (readOnly) return (
    <li className="signal-criterion-card">
      <div className="signal-criterion-card__number" aria-hidden="true">{index + 1}</div>
      <div className="signal-criterion-card__body signal-copy-flow">
        <div className="signal-criterion-card__heading"><h3>Criterion {index + 1}</h3></div>
        <p>{row.text}</p>
        <p><strong>Type: </strong>{TYPE_LABELS[row.type]}</p>
        <p><strong>Evidence expected: </strong>{row.evidence || "Not specified in this version."}</p>
        <p><strong>Relative weight: </strong>{allEqual !== null
          ? `the same for all ${allEqual} criteria`
          : `${row.weight} · ${importance(row.weight, average)} importance`}</p>
        {levels}
      </div>
    </li>
  );
  return (
    <li className="signal-criterion-card">
      <div className="signal-criterion-card__number" aria-hidden="true">{index + 1}</div>
      <div className="signal-criterion-card__body">
        <div className="signal-criterion-card__heading">
          <h3>Criterion {index + 1}</h3>
          {!readOnly && <button type="button" onClick={onRemove} aria-label={`Remove criterion ${index + 1}`}>Remove</button>}
        </div>
        <label className="signal-criterion-field signal-criterion-field--wide">
          <span>Criterion text</span>
          <textarea
            id={`criterion-text-${row.key}`}
            rows={2}
            value={row.text}
            disabled={readOnly}
            aria-label={`Criterion text, criterion ${index + 1}`}
            aria-invalid={textError || Boolean(serverErrors?.text) || undefined}
            aria-describedby={textError ? `criterion-text-error-${row.key}` : serverErrors?.text ? `criterion-text-server-${row.key}` : undefined}
            onChange={(event) => onChange({ text: event.target.value })}
          />
          {textError && <small id={`criterion-text-error-${row.key}`} className="signal-field-error">Enter this criterion's text.</small>}
          {!textError && serverErrors?.text && <small id={`criterion-text-server-${row.key}`} className="signal-field-error">{serverErrors.text}</small>}
        </label>
        <div className="signal-criterion-card__fields">
          <label className="signal-criterion-field">
            <span>Type</span>
            <select
              id={`criterion-type-${row.key}`}
              value={row.type}
              disabled={readOnly}
              aria-label={`Type, criterion ${index + 1}`}
              aria-describedby="criterion-type-guidance"
              onChange={(event) => onChange({ type: event.target.value as CriterionType })}
            >
              {Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="signal-criterion-field signal-criterion-field--grow">
            <span>Evidence expected</span>
            <textarea
              id={`criterion-evidence-${row.key}`}
              rows={2}
              value={row.evidence}
              disabled={readOnly}
              aria-label={`Evidence expected, criterion ${index + 1}`}
              aria-invalid={Boolean(serverErrors?.evidence) || undefined}
              aria-describedby={serverErrors?.evidence ? `criterion-evidence-server-${row.key}` : undefined}
              onChange={(event) => onChange({ evidence: event.target.value })}
            />
            {serverErrors?.evidence && <small id={`criterion-evidence-server-${row.key}`} className="signal-field-error">{serverErrors.evidence}</small>}
          </label>
          <label className="signal-criterion-field signal-criterion-field--weight">
            <span>Relative weight</span>
            <input
              id={`criterion-weight-${row.key}`}
              type="text"
              inputMode="decimal"
              value={row.weight}
              disabled={readOnly}
              aria-label={`Relative weight, criterion ${index + 1}`}
              aria-invalid={weightError || undefined}
              aria-describedby={weightError ? `criterion-weight-error-${row.key}` : undefined}
              onChange={(event) => {
                const raw = event.target.value;
                if (raw === "" || /^\d*\.?\d*$/.test(raw)) onChange({ weight: raw === "" || raw === "." ? 0 : Number(raw) });
              }}
            />
            <span className={`signal-importance signal-importance--${importance(row.weight, average).toLowerCase()}`}>
              {importance(row.weight, average)} importance
            </span>
            {weightError && <small id={`criterion-weight-error-${row.key}`} className="signal-field-error">Weight must be greater than zero.</small>}
          </label>
        </div>
        {levels}
        {serverErrors?.levels && <small id={`criterion-levels-server-${row.key}`} className="signal-field-error">Levels: {serverErrors.levels}</small>}
      </div>
    </li>
  );
}

export function SignalReviewCriteriaPage() {
  const { rubricId } = useParams<{ rubricId: string }>();
  return <CriteriaEditor key={rubricId} id={Number(rubricId)} />;
}

function CriteriaEditor({ id }: { id: number }) {
  const { data: rubric, isPending, isError, error, refetch } = useRubric(id);
  const [uploadOpen, setUploadOpen] = useState(false);
  const save = useSaveCriteria(id);
  const submittedKeys = useRef<string[]>([]);
  const saveErrorRef = useRef<HTMLDivElement>(null);
  // A refused save's summary sits at the bottom of a long editor; bring it into view.
  useEffect(() => {
    if (save.error instanceof ApiError && save.error.status === 422) saveErrorRef.current?.scrollIntoView({ block: "nearest" });
  }, [save.error]);
  const [editedSinceRefusal, setEditedSinceRefusal] = useState<ReadonlySet<string>>(() => new Set());
  const [rows, setRows] = useState<CriterionRow[]>();
  const [savedRows, setSavedRows] = useState<CriterionRow[]>();
  const [attempted, setAttempted] = useState<"save" | "confirm">();
  const [announcement, setAnnouncement] = useState<{ id: number; text: string } | null>(null);
  const [removedStack, setRemovedStack] = useState<Array<{ row: CriterionRow; index: number }>>([]);
  // BUG-566: parser issues the instructor chose to keep as written, sent
  // with the next save; the revision the page's edits started from; and
  // whether activation was refused for items still to check.
  const [pending, setPending] = useState<ReadonlyMap<number, PendingAcknowledgement>>(() => new Map());
  const [loadedRevision, setLoadedRevision] = useState<number | null>(null);
  const [currentIssueId, setCurrentIssueId] = useState<number | null>(null);
  // Set when activation (or, on an active format, any save) was refused for
  // items still to check; `unknown` counts items a refused save would have
  // raised that this page has not seen.
  // `reopened` lists kept items the refused edit would have reopened, and
  // `edits` says in words what that edit was.
  const [blocked, setBlocked] = useState<{ kind: "activate" | "save"; unknown: number; reopened: number[]; edits: string[] } | null>(null);
  // Kept items the server refused to keep without a note.
  const [noteRefused, setNoteRefused] = useState<ReadonlySet<number>>(() => new Set());
  // Kept items an edit opens again. Those a refused save of an active format
  // would open are shown open here, so the instructor can check them again
  // and save the change with them in one go instead of having to undo it.
  const [openAgain, setOpenAgain] = useState<ReadonlyMap<number, OpenedAgain>>(() => new Map());
  const focusHeadingAfterReload = useRef(false);
  const [discardReason, setDiscardReason] = useState<"conflict" | "refused">("conflict");
  const saveRef = useRef<HTMLButtonElement>(null);
  const activeStatusRef = useRef<HTMLDivElement>(null);
  const focusActiveAfterSave = useRef(false);
  const conflictRef = useRef<HTMLDivElement>(null);
  const focusConflictAfterDialog = useRef(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const blockedRef = useRef<HTMLDivElement>(null);
  const announcementSeq = useRef(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  const pendingFocusRef = useRef<{ type: "undo" } | { type: "criterion"; key: string } | null>(null);
  const resolvingBlocker = useRef(false);
  const focusErrorsAfterDialog = useRef(false);
  const focusRefusalAfterDialog = useRef(false);
  // The leave dialog shows a save error only when its own save failed.
  const [leaveSaveFailed, setLeaveSaveFailed] = useState(false);
  useRouteFocus("Review prepared criteria - VERIDICAL", headingRef);

  function announce(text: string) {
    announcementSeq.current += 1;
    setAnnouncement({ id: announcementSeq.current, text });
  }
  const announceRef = useRef(announce);
  announceRef.current = announce;

  // A layout effect, so the focus runs in the commit that draws the new
  // control. A passive effect left over from an earlier render can run
  // after the click handler but before that commit, and would use up the
  // request while the control is still missing.
  useLayoutEffect(() => {
    const target = pendingFocusRef.current;
    if (!target) return;
    pendingFocusRef.current = null;
    if (target.type === "undo") {
      undoRef.current?.focus();
    } else if (target.type === "criterion") {
      document.getElementById(`criterion-text-${target.key}`)?.focus();
    }
  }, [rows, removedStack]);

  useEffect(() => {
    if (!rubric || rows) return;
    // A stored split such as 11.111 is shown, and saved, as plain relative
    // weights with the same ratios.
    const weights = readableWeights(rubric.criteria.map((criterion) => criterion.weight));
    const next = rubric.criteria.map((criterion, index) => ({
      key: String(criterion.id),
      id: criterion.id,
      type: criterion.type,
      text: criterion.text,
      evidence: criterion.evidence ?? "",
      weight: weights[index],
      levels: criterion.levels ?? null,
    }));
    setRows(next);
    setSavedRows(next);
    setLoadedRevision(rubric.revision ?? null);
  }, [rows, rubric]);

  const readOnly = rubric ? !rubric.is_latest_version || rubric.can_edit_criteria === false : true;
  const rowsChanged = rows !== undefined && savedRows !== undefined && JSON.stringify(rows) !== JSON.stringify(savedRows);
  const dirty = !readOnly && rows !== undefined && savedRows !== undefined && (rowsChanged || pending.size > 0);
  // The leave dialog names what would be lost.
  const onlyKeepsUnsaved = !rowsChanged && pending.size > 0;

  // Every change undone: the items it would have opened again stay checked
  // as they were, and a new check made for them is not needed. Say so,
  // rather than let a later save drop it without a word.
  useEffect(() => {
    if (rowsChanged || ![...openAgain.values()].some((item) => item.kind === "unsaved")) return;
    const ids = [...openAgain.entries()].filter(([, item]) => item.kind === "unsaved").map(([issueId]) => issueId);
    setOpenAgain((current) => new Map([...current].filter(([, item]) => item.kind !== "unsaved")));
    setPending((current) => {
      if (!ids.some((issueId) => current.has(issueId))) return current;
      const next = new Map(current);
      for (const issueId of ids) next.delete(issueId);
      return next;
    });
    setBlocked((current) => current?.kind === "save" ? null : current);
    announceRef.current(`Your changes are undone, so ${ids.length === 1 ? "the item you checked stays" : `the ${ids.length} items you checked stay`} checked as before.`);
  }, [rowsChanged, openAgain]);

  useEffect(() => {
    if (!focusHeadingAfterReload.current || rows === undefined) return;
    focusHeadingAfterReload.current = false;
    headingRef.current?.focus();
  }, [rows]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!dirty) return;
    function beforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  // Activation refused for items still to check: take the instructor to the
  // list of them once it is on the page.
  const focusBlockedSummary = useRef(false);
  // Layout, for the same reason as the focus request above: a stale passive
  // run must not clear the request before the summary has been drawn.
  useLayoutEffect(() => {
    if (!blocked) { focusBlockedSummary.current = false; return; }
    // Behind the leave dialog the page is inert; wait until it has closed.
    if (!focusBlockedSummary.current || !blockedRef.current || blocker.state === "blocked") return;
    focusBlockedSummary.current = false;
    requestAnimationFrame(() => {
      blockedRef.current?.scrollIntoView({ block: "nearest" });
      blockedRef.current?.focus({ preventScroll: true });
    });
  });

  useEffect(() => {
    if (!noteRefused.size || blocker.state === "blocked") return;
    requestAnimationFrame(() => {
      const note = document.querySelector<HTMLTextAreaElement>(".signal-keep-form[data-refused] textarea");
      note?.scrollIntoView({ block: "center" });
      note?.focus({ preventScroll: true });
    });
  }, [noteRefused, blocker.state]);

  useEffect(() => {
    if (!focusActiveAfterSave.current || !rubric?.is_active || !activeStatusRef.current) return;
    focusActiveAfterSave.current = false;
    // The activate button is gone once the format is active; say so where it was.
    activeStatusRef.current.scrollIntoView({ block: "nearest" });
    activeStatusRef.current.focus({ preventScroll: true });
  });

  useEffect(() => {
    if (blocker.state !== "blocked") setLeaveSaveFailed(false);
    if (blocker.state === "blocked") resolvingBlocker.current = false;
    else if (focusErrorsAfterDialog.current) {
      focusErrorsAfterDialog.current = false;
      queueMicrotask(() => summaryRef.current?.focus());
    } else if (focusRefusalAfterDialog.current) {
      focusRefusalAfterDialog.current = false;
      requestAnimationFrame(() => { saveErrorRef.current?.scrollIntoView({ block: "nearest" }); saveErrorRef.current?.focus(); });
    } else if (focusConflictAfterDialog.current) {
      focusConflictAfterDialog.current = false;
      requestAnimationFrame(() => { conflictRef.current?.scrollIntoView({ block: "nearest" }); conflictRef.current?.focus(); });
    }
  }, [blocker.state]);

  if (Number.isFinite(id) && (isPending || (!isError && rubric && !rows))) {
    return <div className="signal-route signal-page-flow"><CriteriaHeader headingRef={headingRef} /><div className="signal-desk-loading" role="status" aria-busy="true"><span>Loading prepared criteria…</span><i /><i /></div></div>;
  }
  if (!Number.isFinite(id) || isError || !rubric || !rows) {
    const isNotFound = !Number.isFinite(id) || (error instanceof ApiError && error.status === 404);
    return (
      <div className="signal-route signal-page-flow">
        <CriteriaHeader headingRef={headingRef} />
        {isNotFound ? (
          <Alert title="Format not available" tone="warning" role="alert">
            <p>This format version does not exist or is not available to your account.</p>
            <Link className="signal-button signal-button--secondary" to="/rubric">Back to Required format</Link>
          </Alert>
        ) : (
          <Alert title="Could not load these criteria" tone="error" role="alert">
            <Button variant="secondary" onClick={() => refetch()}>Try again</Button>
          </Alert>
        )}
      </div>
    );
  }

  const currentRows = rows;
  const details = rubric.parse_issue_details
    ? rubric.parse_issue_details.map((issue) => openAgain.get(issue.id)?.kind === "unsaved" && issue.status === "acknowledged"
      ? { ...issue, status: "open" as const, resolution: null } : issue)
    : null;
  const openedAgain = details ? details.filter((issue) => openAgain.get(issue.id)?.kind === "unsaved" && issue.status === "open") : [];
  // Issues on a criterion removed on this page are settled by the next save.
  const pageIssues = details
    ? orderedIssues(details, currentRows.map((row) => row.id))
      .filter((issue) => issue.criterion_id === null || currentRows.some((row) => row.id === issue.criterion_id))
    : [];
  const openItems = openIssues(pageIssues, pending);
  const unsavedKeeps = pageIssues.filter((issue) => issue.status === "open" && pending.has(issue.id)).length;
  const reviewState = formatReviewState(rubric);
  // While a refused save of an active format is showing, the counters say
  // so instead of "no items to check".
  const refusedCount = blocked?.kind === "save" && !openItems.length ? blocked.unknown + blocked.reopened.length : 0;
  const reopenedItems = blocked ? pageIssues.filter((issue) => blocked.reopened.includes(issue.id)) : [];
  const formatIssues = pageIssues.filter((issue) => issue.criterion_id === null);
  const issuesFor = (row: CriterionRow) => row.id === null ? [] : pageIssues.filter((issue) => issue.criterion_id === row.id);
  const legacy = details === null && currentRows.some((row) => row.levels?.length)
    && currentRows.every((row) => !row.levels?.some((level) => level.source));
  const checked = details !== null || currentRows.some((row) => row.levels?.some((level) => level.source));
  const average = currentRows.length
    ? currentRows.reduce((total, row) => total + row.weight, 0) / currentRows.length
    : 0;
  const problems = attempted
    ? [
      ...(currentRows.length === 0 ? [{ key: "empty", message: "Add at least one criterion.", target: addRef }] : []),
      ...currentRows.flatMap((row, index) => [
        ...(row.text.trim() === "" ? [{ key: `text-${row.key}`, message: `Criterion ${index + 1} has no text.`, targetId: `criterion-text-${row.key}` }] : []),
        ...(invalidWeight(row.weight) ? [{ key: `weight-${row.key}`, message: `Criterion ${index + 1}'s weight must be greater than zero.`, targetId: `criterion-weight-${row.key}` }] : []),
      ]),
    ]
    : [];

  function criterionName(row: CriterionRow, index: number): string {
    return shortName(row.text, `Criterion ${index + 1}`);
  }

  function issuePlace(issue: RubricIssueDetail): { label: string; anchor: string } {
    const index = currentRows.findIndex((row) => row.id !== null && row.id === issue.criterion_id);
    const row = index >= 0 ? currentRows[index] : undefined;
    const level = issue.level === null ? undefined : row?.levels?.find((item) => item.level === issue.level);
    if (!row) return { label: "The whole format", anchor: levelAnchorId("format", null) };
    return { label: `${criterionName(row, index)}${level ? ` · ${level.name}` : ""}`, anchor: levelAnchorId(row.key, level ? level.level : null) };
  }

  // Worded as on the item's own card.
  function itemCopy(issue: RubricIssueDetail): string {
    const levels = currentRows.find((row) => row.id !== null && row.id === issue.criterion_id)?.levels ?? [];
    return levelIssueCopy(issue, levels.find((item) => item.level === issue.level), levels);
  }

  function goToIssue(issue: RubricIssueDetail) {
    const place = issuePlace(issue);
    const target = document.getElementById(place.anchor);
    if (!target) return;
    const details = target.closest("details");
    if (details) details.open = true;
    setCurrentIssueId(issue.id);
    target.scrollIntoView({ block: "center" });
    target.focus({ preventScroll: true });
    const position = openItems.findIndex((item) => item.id === issue.id);
    announce(position >= 0 ? `Item ${position + 1} of ${openItems.length} to check: ${place.label}.` : `${place.label}.`);
  }

  function goToSave() {
    saveRef.current?.scrollIntoView({ block: "center" });
    saveRef.current?.focus({ preventScroll: true });
  }

  function showRefusal() {
    blockedRef.current?.scrollIntoView({ block: "nearest" });
    blockedRef.current?.focus({ preventScroll: true });
  }

  function forgetNoteRefusals(issues: readonly RubricIssueDetail[]) {
    setNoteRefused((current) => {
      if (!issues.some((issue) => current.has(issue.id))) return current;
      const next = new Set(current);
      for (const issue of issues) next.delete(issue.id);
      return next;
    });
  }

  // The next open item after the given issues, in page order.
  function nextAfter(issues: readonly RubricIssueDetail[]): RubricIssueDetail | null {
    const ids = new Set(issues.map((issue) => issue.id));
    const last = pageIssues.reduce((found, issue, index) => ids.has(issue.id) ? index : found, -1);
    return pageIssues.slice(last + 1).find((issue) => openItems.includes(issue)) ?? null;
  }

  function freshLevels(row: CriterionRow): RubricLevel[] {
    const fresh = row.id === null ? undefined : rubric!.criteria.find((criterion) => criterion.id === row.id)?.levels;
    return (row.levels ?? []).map((level) => {
      const latest = fresh?.find((item) => item.level === level.level && item.descriptor === level.descriptor);
      return latest ? { ...level, source: latest.source } : level;
    });
  }

  function keepIssues(issues: readonly RubricIssueDetail[], reason: RubricAcknowledgementReason, note: string | null) {
    setPending((current) => {
      const next = new Map(current);
      for (const issue of issues) next.set(issue.id, { issue_id: issue.id, reason_code: reason, note });
      return next;
    });
    forgetNoteRefusals(issues);
    announce("Wording kept, not saved yet.");
  }

  function undoKeep(issues: readonly RubricIssueDetail[]) {
    setPending((current) => {
      const next = new Map(current);
      for (const issue of issues) next.delete(issue.id);
      return next;
    });
    forgetNoteRefusals(issues);
    announce("Kept wording undone.");
  }

  const levelReview = (row: CriterionRow, index: number): LevelReview => ({
    name: criterionName(row, index), levels: freshLevels(row), issues: issuesFor(row), pending, nextAfter, onGo: goToIssue,
    onKeep: keepIssues, onUndoKeep: undoKeep, onSave: goToSave, noteRefused, openAgain,
    onPageShown: (page) => announce(`Page ${page} shown in the original format.`),
  });

  // A 409 for items still to check. On an active format nothing was saved;
  // on activation the server saved the edits and kept wording first, and
  // says which revision it stored.
  function refusedForOpenItems(error: ApiError, submitted: CriterionRow[]) {
    const revision = typeof error.details.revision === "number" ? error.details.revision : null;
    const ids = Array.isArray(error.details.open_issue_ids) ? error.details.open_issue_ids : [];
    focusBlockedSummary.current = true;
    if (rubric!.is_active) {
      // Every refused item the page is not already listing as open: either a
      // kept item the edit would reopen, or one that would newly fail.
      const listed = new Set(openItems.map((issue) => issue.id));
      const refused = ids.filter((id): id is number => typeof id === "number" && !listed.has(id));
      // Kept and saved items open here, to be checked again in the same save.
      const kept = refused.filter((id) => pageIssues.some((issue) => issue.id === id && issue.status === "acknowledged"));
      const reopened = refused.filter((id) => pending.has(id));
      const edits = describeEdits(submitted, savedRows ?? []);
      if (kept.length) setOpenAgain((current) => new Map([...current, ...kept.map((id) => [id, { kind: "unsaved" as const, changes: edits }] as const)]));
      setBlocked({ kind: "save", unknown: refused.length - kept.length - reopened.length, reopened, edits });
      announce(kept.length ? "Nothing was saved. Check the items this change opens again, then save." : "Nothing was saved. Check these items first.");
      return;
    }
    setSavedRows(submitted);
    setRemovedStack([]);
    setBlocked({ kind: "activate", unknown: 0, reopened: [], edits: [] });
    announce("Your changes were saved. The format was not activated.");
    if (revision !== null) setLoadedRevision(revision);
    void refetch().then((result) => {
      if (!result.isSuccess) return;
      // The server stored the kept wording; the fresh read now shows it.
      setPending(new Map());
      if (revision === null) setLoadedRevision(result.data.revision ?? null);
    });
  }

  // Any save of an active format, and any activation, waits until every item
  // is kept or settled. The server refuses it too; this saves a round trip.
  function blockedBeforeSaving(confirm: boolean): boolean {
    if (!openItems.length || !(rubric!.is_active || confirm)) return false;
    focusBlockedSummary.current = true;
    setBlocked({ kind: rubric!.is_active ? "save" : "activate", unknown: 0, reopened: [], edits: [] });
    return true;
  }

  function bodyFor(submitted: CriterionRow[], confirm: boolean) {
    // Only issues still open on the server are sent; the rest were settled.
    const acknowledgements = [...pending.values()].filter((item) => pageIssues.some((issue) => issue.id === item.issue_id && issue.status === "open"));
    return {
      criteria: toEdits(submitted), confirm,
      ...(loadedRevision !== null ? { expected_revision: loadedRevision } : {}),
      ...(acknowledgements.length ? { acknowledgements } : {}),
    };
  }

  function savedAnnouncement(out: { parse_issue_details?: RubricIssueDetail[] | null }, confirm: boolean, reopened: number, unrecorded: number) {
    const notRecorded = unrecorded
      ? ` ${unrecorded === 1 ? "One item" : `${unrecorded} items`} you checked again stayed checked as before, because nothing ${unrecorded === 1 ? "it was" : "they were"} checked against changed, so the new note was not recorded.`
      : "";
    if (confirm) return `Format confirmed and activated.${notRecorded}`;
    // An active format's changes are live as soon as they are saved.
    if (rubric!.is_active) return `Saved. New checks use this version.${notRecorded}`;
    if (!out.parse_issue_details) return "Draft saved.";
    const left = out.parse_issue_details.filter((issue) => issue.status === "open").length;
    const opened = reopened
      ? ` Your change opened ${reopened === 1 ? "an item" : `${reopened} items`} you checked before again; check ${reopened === 1 ? "it" : "them"} again below.`
      : "";
    return left
      ? `Draft saved.${opened} ${left} ${left === 1 ? "item" : "items"} to check ${left === 1 ? "remains" : "remain"}.${notRecorded}`
      : `Draft saved.${notRecorded}`;
  }

  // 422 acknowledgement_note_required: nothing was saved. Each listed keep
  // opens again at its note.
  function refusedForMissingNotes(error: ApiError): boolean {
    if (error.code !== "acknowledgement_note_required") return false;
    const ids = Array.isArray(error.details.issue_ids) ? error.details.issue_ids.filter((id): id is number => typeof id === "number") : [];
    setNoteRefused(new Set(ids.filter((id) => pending.has(id))));
    announce(`Nothing was saved. ${ids.length === 1 ? "One kept item needs" : `${ids.length} kept items need`} a note saying what you compared.`);
    return true;
  }

  function discardAndReload() {
    setDiscardOpen(false);
    save.reset();
    void refetch().then((result) => {
      // Reload only from a fresh read, never from the cached copy.
      if (!result.isSuccess) return;
      setPending(new Map());
      setNoteRefused(new Set());
      setOpenAgain(new Map());
      setRemovedStack([]);
      setBlocked(null);
      setAttempted(undefined);
      // The control that asked for this is gone; start again from the top.
      focusHeadingAfterReload.current = true;
      setRows(undefined);
      setSavedRows(undefined);
      announce("Your changes were discarded. The page shows the saved version.");
    });
  }

  function updateRow(key: string, patch: Partial<CriterionRow>) {
    setRows((current) => current?.map((row) => row.key === key ? { ...row, ...patch } : row));
    // Changing a refused field clears that field's message; levels are not
    // edited here, so their refusal stays until the next save.
    const changed = (["text", "evidence"] as const).filter((field) => field in patch).map((field) => `${key}.${field}`);
    if (changed.length) setEditedSinceRefusal((current) => new Set([...current, ...changed]));
  }

  function addCriterion() {
    const next: CriterionRow = { key: `new-${++localCriterionId}`, id: null, type: "structural", text: "", evidence: "", weight: 0, levels: null };
    setRows((current) => [...(current ?? []), next]);
    announce(`Criterion added. ${currentRows.length + 1} criteria total.`);
  }

  function removeCriterion(key: string) {
    const index = currentRows.findIndex((row) => row.key === key);
    const removed = index >= 0 ? currentRows[index] : undefined;
    setRows((current) => current?.filter((row) => row.key !== key));
    if (removed) {
      setRemovedStack((prev) => [...prev, { row: removed, index }]);
    }
    announce("Criterion removed.");
    pendingFocusRef.current = { type: "undo" };
  }

  function undoRemoveCriterion() {
    const last = removedStack[removedStack.length - 1];
    if (!last) return;
    setRemovedStack((prev) => prev.slice(0, -1));
    setRows((current) => {
      const list = [...(current ?? [])];
      const insertAt = Math.min(Math.max(0, last.index), list.length);
      list.splice(insertAt, 0, last.row);
      return list;
    });
    announce("Criterion restored.");
    pendingFocusRef.current = { type: "criterion", key: last.row.key };
  }

  function distributeWeights() {
    if (!currentRows.length) return;
    // Weights are relative: equal weights are 1 each, never 100 split n ways.
    setRows((current) => current?.map((row) => ({ ...row, weight: 1 })));
    announce("Relative weights set equally across all criteria.");
  }

  function attemptSave(confirm: boolean) {
    if (save.isPending) return;
    setAttempted(confirm ? "confirm" : "save");
    setBlocked(null);
    if (invalidCriteria(currentRows)) {
      queueMicrotask(() => summaryRef.current?.focus());
      return;
    }
    if (blockedBeforeSaving(confirm)) return;
    const submitted = currentRows;
    submittedKeys.current = submitted.map((row) => row.key);
    setEditedSinceRefusal(new Set());
    const body = bodyFor(submitted, confirm);
    const checkedBefore = new Set(rubric!.parse_issue_details?.filter((issue) => issue.status === "acknowledged").map((issue) => issue.id) ?? []);
    const edits = describeEdits(submitted, savedRows ?? []);
    save.mutate(
      body,
      {
        onSuccess: (out) => {
          const after = out.parse_issue_details ?? [];
          // Items this saved change opened again, said on the item itself.
          const reopened = after.filter((issue) => issue.status === "open" && checkedBefore.has(issue.id));
          // A check sent for an item the server found unchanged is not recorded.
          const unrecorded = (body.acknowledgements ?? []).filter((ack) => {
            const issue = after.find((item) => item.id === ack.issue_id);
            return issue?.status === "acknowledged" && issue.resolution?.note !== ack.note;
          }).length;
          setSavedRows(submitted);
          setRemovedStack([]);
          setPending(new Map());
          setNoteRefused(new Set());
          setOpenAgain(new Map(reopened.map((issue) => [issue.id, { kind: "saved" as const, changes: edits }])));
          setLoadedRevision(out.revision ?? null);
          if (confirm) focusActiveAfterSave.current = true;
          announce(savedAnnouncement(out, confirm, reopened.length, unrecorded));
        },
        onError: (error) => {
          if (!(error instanceof ApiError)) return;
          if (error.code === "unresolved_rubric_issues") refusedForOpenItems(error, submitted);
          else refusedForMissingNotes(error);
        },
      },
    );
  }

  function resolveBlocker(action: "keep" | "leave") {
    if (resolvingBlocker.current || blocker.state !== "blocked") return;
    resolvingBlocker.current = true;
    if (action === "keep") blocker.reset();
    else blocker.proceed();
  }

  function saveAndLeave() {
    if (resolvingBlocker.current || blocker.state !== "blocked") return;
    setLeaveSaveFailed(false);
    setAttempted("save");
    if (invalidCriteria(currentRows)) {
      focusErrorsAfterDialog.current = true;
      resolveBlocker("keep");
      return;
    }
    if (blockedBeforeSaving(false)) { resolveBlocker("keep"); return; }
    resolvingBlocker.current = true;
    const submitted = currentRows;
    submittedKeys.current = submitted.map((row) => row.key);
    setEditedSinceRefusal(new Set());
    save.mutate(
      bodyFor(submitted, false),
      {
        onSuccess: () => { setSavedRows(submitted); setPending(new Map()); blocker.proceed(); },
        onError: (error) => {
          resolvingBlocker.current = false;
          setLeaveSaveFailed(true);
          // The refused fields are on the page, not in the dialog: go back to them.
          if (error instanceof ApiError && refusedForMissingNotes(error)) resolveBlocker("keep");
          else if (error instanceof ApiError && error.status === 422) { focusRefusalAfterDialog.current = true; resolveBlocker("keep"); }
          else if (error instanceof ApiError && error.code === "unresolved_rubric_issues") { refusedForOpenItems(error, submitted); resolveBlocker("keep"); }
          else if (error instanceof ApiError && (error.code === "rubric_changed" || error.code === "rubric_locked")) { focusConflictAfterDialog.current = true; resolveBlocker("keep"); }
        },
      },
    );
  }

  const { byRow: fieldErrors, other: otherRefusals, count: refusalCount } = serverFieldErrors(save.error, submittedKeys.current);
  // Every refused field still unchanged since the save, in page order.
  const refused = currentRows.flatMap((row, index) =>
    (Object.entries(fieldErrors.get(row.key) ?? {}) as [ServerField, string][])
      .filter(([field]) => !editedSinceRefusal.has(`${row.key}.${field}`))
      .map(([field, message]) => ({ key: row.key, index, field, message })));
  // Every refusal counts once; a changed field takes its own refusals off the count.
  const changedRefusals = currentRows.reduce((total, row) => total + (["text", "evidence"] as const)
    .filter((field) => fieldErrors.get(row.key)?.[field] && editedSinceRefusal.has(`${row.key}.${field}`)).length, 0);
  const needing = Math.max(0, refusalCount - changedRefusals);
  // A refused save keeps nothing: the whole set was not saved.
  const conflict = save.error instanceof ApiError && save.error.status === 409
    && ["unresolved_rubric_issues", "rubric_changed", "rubric_locked"].includes(save.error.code) ? save.error.code : null;
  // Shown at each kept item's note instead of as a general error.
  const notesRefused = save.error instanceof ApiError && save.error.code === "acknowledgement_note_required";
  const saveError = conflict || notesRefused ? undefined : save.error instanceof ApiError && save.error.status === 422
    ? fieldErrors.size > 0 || otherRefusals.length > 0
      ? needing > 0 ? `Nothing was saved. ${needing === 1 ? "One value needs" : `${needing} values need`} changing. Your edits are still on this page.`
        : "Nothing was saved. You have changed the values that were refused; save again to check them."
      : "One or more criterion values were rejected. Your edits are still on this page. Review the criteria and try again."
    : save.error instanceof Error ? save.error.message : save.error ? "Save failed. Try again." : undefined;
  const refusedList = needing > 0 && <ul className="signal-refused-fields">
    {refused.map((item) => <li key={`${item.key}-${item.field}`}>
      <button type="button" className="signal-text-link" onClick={() => focusRefusedField(item.key, item.field)}>
        Criterion {item.index + 1}, {SERVER_FIELD_LABEL[item.field]}</button>: {item.message}
    </li>)}
    {otherRefusals.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}
  </ul>;

  return (
    <div className="signal-route signal-page-flow signal-criteria-review">
      <CriteriaHeader headingRef={headingRef} title={rubric.title} count={currentRows.length} readOnly={readOnly} familyId={rubric.rubric_family_id} version={rubric.version}
        instruction={readOnly ? "Inspect this version’s criteria beside its uploaded format." : "Compare the prepared criteria with your uploaded format. Correct missing or extra requirements before confirming."} />
      <FormatReviewWorkbench rubric={rubric} onUploadNewVersion={() => setUploadOpen(true)}>
      <div className="signal-section-flow signal-criteria-guidance">
        {(checked || legacy) && <RubricReviewStatus total={pageIssues.length} open={openItems.length} unsaved={unsavedKeeps}
          refused={refusedCount} readOnly={readOnly} isActive={rubric.is_active} state={legacy ? { kind: "legacy" } : reviewState.kind === "legacy" ? { kind: "all_match" } : reviewState}
          onFirst={() => openItems[0] && goToIssue(openItems[0])} onSave={goToSave} />}
        <SourceReadingSummary summary={rubric.source_summary} />
        {readOnly
          ? <Alert title="Read-only version" tone="info">
              <p>{rubric.is_latest_version
                ? "A check already uses this version. Its criteria are locked so saved results do not change."
                : "A newer version exists. Reports remain pinned to this history version for traceability."}</p>
              <p>Upload a new version to make corrections. Existing reports keep their original criteria.</p>
              <Button variant="secondary" onClick={() => setUploadOpen(true)}>Upload new version</Button>
            </Alert>
          : null}
        <details className="signal-copy-flow">
          <summary>How criterion types work</summary>
          <p id="criterion-type-guidance">Choose Structural for checks based on document structure or presence, Semantic for checks that require interpreting the content, and Not from the document for requirements that need other evidence, such as a presentation or printed copy.</p>
        </details>
        {details === null && rubric.parse_status === "needs_review" && (
          <Alert title="Needs manual completion" tone="warning" role="status">
            <p>{readOnly
              ? "This version retains parser uncertainty. Check the original format and upload a new version if corrections are needed."
              : rubric.is_active ? "This format was activated with parser uncertainty. Review and save any corrections." : "VERIDICAL could not confirm every criterion after several attempts. Check each one against the original format before confirming."}</p>
            {rubric.parse_issues?.length ? <ul>{rubric.parse_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
          </Alert>
        )}
        {blocked && openItems.length + blocked.unknown + blocked.reopened.length > 0 && (
          <div ref={blockedRef} tabIndex={-1} role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>{blocked.kind === "save" ? "Check these items before saving changes to this active format" : "Check these items before activating"}</h2>
            {blocked.kind === "save" && <p>Nothing was saved. Your edits are still on this page.</p>}
            {blocked.kind === "save" && openedAgain.length > 0 && <p>
              Your change would open {openedAgain.length === 1 ? "an item" : `${openedAgain.length} items`} you checked before
              again, because what {openedAgain.length === 1 ? "it was" : "they were"} checked against would change.
              Check {openedAgain.length === 1 ? "it" : "them"} again below, then save.</p>}
            {openItems.length > 0 && <ul>{openItems.map((issue) => <li key={issue.id}><button type="button" onClick={() => goToIssue(issue)}>
              {issuePlace(issue).label}: {itemCopy(issue)}</button></li>)}</ul>}
            {reopenedItems.length > 0 && <>
              <p>Saving would reopen {reopenedItems.length === 1 ? "this item" : "these items"} you kept, because the wording {reopenedItems.length === 1 ? "it was" : "they were"} kept for would change:</p>
              <ul>{reopenedItems.map((issue) => <li key={issue.id}><button type="button" onClick={() => goToIssue(issue)}>
                {issuePlace(issue).label}: {itemCopy(issue)}</button></li>)}</ul>
            </>}
            {blocked.unknown > 0 && <p>Saving would also leave {blocked.unknown === 1 ? "one level description" : `${blocked.unknown} level descriptions`} that no longer {blocked.unknown === 1 ? "matches" : "match"} the document.</p>}
            {blocked.edits.length > 0 && <>
              <p>The changes that were not saved:</p>
              <ul className="signal-refused-edits">{blocked.edits.map((edit) => <li key={edit}>{edit}</li>)}</ul>
            </>}
            {blocked.kind === "save" && <div className="signal-control-cluster">
              <p>{openedAgain.length > 0
                ? "Or discard your changes and keep the saved version."
                : "Change these back and save again, or discard your changes."}</p>
              <Button variant="secondary" onClick={() => { setDiscardReason("refused"); setDiscardOpen(true); }}>Discard my changes</Button>
            </div>}
          </div>
        )}
        {notesRefused && noteRefused.size > 0 && (
          <div role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>Add a note to {noteRefused.size === 1 ? "this kept item" : "these kept items"}</h2>
            <p>Nothing was saved. VERIDICAL keeps wording it could not trace to the document only with a note saying what you compared.</p>
            <ul>{pageIssues.filter((issue) => noteRefused.has(issue.id)).map((issue) => <li key={issue.id}><button type="button" onClick={() => goToIssue(issue)}>
              {issuePlace(issue).label}</button></li>)}</ul>
          </div>
        )}
        {formatIssues.length > 0 && <section className="signal-format-issues" aria-labelledby="format-issues-heading">
          <h3 id="format-issues-heading">Items about the whole format</h3>
          <CriterionLevels criterionKey="format" criterionName="The whole format" levels={[]} issues={formatIssues} pending={pending}
            readOnly={readOnly} onKeep={keepIssues} onUndoKeep={undoKeep} onPageShown={(page) => announce(`Page ${page} shown in the original format.`)}
            nextAfter={nextAfter} onGo={goToIssue} onSave={goToSave} noteRefused={noteRefused} openAgain={openAgain}
            rowHeading="From reading the format" wholeFormat formatPage={rubric.source_summary?.copy_pages[0] ?? null} />
        </section>}
        {problems.length > 0 && (
          <div ref={summaryRef} tabIndex={-1} role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>Fix these items before {attempted === "confirm" ? "confirming" : "saving"}</h2>
            <ul>{problems.map((problem) => <li key={problem.key}><button type="button" onClick={() => { if ("targetId" in problem && problem.targetId) document.getElementById(problem.targetId)?.focus(); else addRef.current?.focus(); }}>{problem.message}</button></li>)}</ul>
          </div>
        )}
      </div>
      {pageIssues.length > 0 && <ReviewItemNavigator items={openItems} currentId={currentIssueId} unsaved={unsavedKeeps}
        refused={refusedCount} readOnly={readOnly} onGo={goToIssue} onSave={goToSave} onShowRefusal={showRefusal} />}
      {currentRows.length > 0 ? (
        <ol className="signal-criteria-list" aria-label="Rubric criteria">
          {currentRows.map((row, index) => (
            <CriterionCard
              key={row.key}
              row={row}
              index={index}
              average={average}
              allEqual={equalWeights(currentRows.map((item) => item.weight)) ? currentRows.length : null}
              readOnly={readOnly}
              showErrors={attempted !== undefined}
              onChange={(patch) => updateRow(row.key, patch)}
              onRemove={() => removeCriterion(row.key)}
              serverErrors={unchangedRefusals(fieldErrors.get(row.key), row.key, editedSinceRefusal)}
              review={levelReview(row, index)}
            />
          ))}
        </ol>
      ) : (
        <div className="signal-desk-empty"><h2>No criteria yet</h2><p>Add at least one criterion before saving.</p></div>
      )}
      <div className="signal-copy-flow signal-criteria-save-region">
        <div className="signal-criteria-feedback" aria-live="polite" aria-atomic="true">
          {announcement && <span key={announcement.id}>{announcement.text}</span>}
          {removedStack.length > 0 && !readOnly && (
            <Button ref={undoRef} variant="quiet" onClick={undoRemoveCriterion}>Undo remove</Button>
          )}
        </div>
        {!readOnly && (
          <div className="signal-save-dock">
            <div>
              <Button ref={addRef} variant="quiet" onClick={addCriterion}>Add criterion</Button>
              <Button variant="secondary" onClick={distributeWeights}>Set equal relative weights</Button>
              <p>Weights are relative. VERIDICAL shows importance as Low, Medium, or High.</p>
              <p>Saving or confirming requires text and a relative weight greater than zero for every criterion. Evidence expected is optional.</p>
              {details !== null && !rubric.is_active && <p>You can save a draft at any time. Activation waits until each item is checked and kept, or no longer applies.</p>}
              {rubric.is_active && <p>This format is active. Saved changes apply to new checks straight away.</p>}
            </div>
            <div>
              <Button
                ref={saveRef}
                variant="secondary"
                aria-busy={save.isPending || undefined}
                aria-disabled={save.isPending || undefined}
                onClick={() => attemptSave(false)}
              >
                {rubric.is_active ? "Save changes" : "Save draft"}
              </Button>
              {!rubric.is_active && <Button
                variant="brand"
                aria-busy={save.isPending || undefined}
                aria-disabled={save.isPending || undefined}
                data-tour="confirm-rubric-cta"
                onClick={() => attemptSave(true)}
              >
                Confirm and activate format
              </Button>}
            </div>
          </div>
        )}
        {save.isPending && <p role="status">Saving criteria…</p>}
        {saveError && <div ref={saveErrorRef} tabIndex={-1}><Alert title="Could not save these criteria" tone="error" role="alert">{saveError}{refusedList}</Alert></div>}
        <div ref={conflictRef} tabIndex={-1}>
        {conflict === "rubric_changed" && <Alert title="This format changed somewhere else" tone="error" role="alert">
          <p>This format changed in another tab or window. Your edits are still on this page but were not saved.</p>
          <Button variant="secondary" onClick={() => { setDiscardReason("conflict"); setDiscardOpen(true); }}>Load the latest version</Button>
        </Alert>}
        {conflict === "rubric_locked" && <Alert title="This version is locked" tone="error" role="alert">
          <p>A check started using this version, so it is now locked. Your edits were not saved.</p>
          <p>Upload a new version to make corrections. Existing reports keep their original criteria.</p>
          <Button variant="secondary" onClick={() => setUploadOpen(true)}>Upload new version</Button>
        </Alert>}
        </div>
        {rubric.is_active && <div ref={activeStatusRef} tabIndex={-1}><Alert title="This format is active" tone="success">New checks use this version.</Alert></div>}
      </div>
      </FormatReviewWorkbench>
      {blocker.state === "blocked" && (
        <Dialog
          title="Leave without saving your changes?"
          onClose={() => resolveBlocker("keep")}
          actions={<><Button variant="secondary" disabled={save.isPending} onClick={() => resolveBlocker("keep")}>Keep editing</Button><Button variant="danger" disabled={save.isPending} onClick={() => resolveBlocker("leave")}>Leave without saving</Button><Button variant="brand" busy={save.isPending} onClick={saveAndLeave}>{rubric.is_active ? "Save changes and leave" : "Save draft and leave"}</Button></>}
        >
          <div className="signal-dialog-copy"><p>{onlyKeepsUnsaved
            ? "The items you kept or marked as checked on this page are not saved yet, and will be lost if you leave now."
            : "Your unsaved criteria changes will be discarded if you leave now."}</p>{leaveSaveFailed && saveError && <Alert title="Could not save" tone="error" role="alert">{saveError}</Alert>}</div>
        </Dialog>
      )}
      {discardOpen && (discardReason === "conflict" ? (
        <Dialog
          title="Discard your edits and load the latest version?"
          onClose={() => setDiscardOpen(false)}
          actions={<><Button variant="secondary" onClick={() => setDiscardOpen(false)}>Keep editing</Button><Button variant="danger" onClick={discardAndReload}>Discard and load</Button></>}
        >
          <div className="signal-dialog-copy"><p>Your unsaved changes on this page, including wording you chose to keep, will be replaced by the version saved elsewhere.</p></div>
        </Dialog>
      ) : (
        <Dialog
          title="Discard your changes?"
          onClose={() => setDiscardOpen(false)}
          actions={<><Button variant="secondary" onClick={() => setDiscardOpen(false)}>Keep editing</Button><Button variant="danger" onClick={discardAndReload}>Discard changes</Button></>}
        >
          <div className="signal-dialog-copy"><p>Your unsaved criteria changes, and any wording you kept but have not saved, will be removed. The page will show the saved version of this format.</p></div>
        </Dialog>
      ))}
      {uploadOpen && <UploadRubricModal familyId={rubric.rubric_family_id} onClose={() => setUploadOpen(false)} />}
    </div>
  );
}
