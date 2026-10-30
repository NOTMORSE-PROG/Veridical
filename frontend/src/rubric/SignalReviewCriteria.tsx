import { useEffect, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router";
import { ApiError } from "../api/client";
import type { CriterionType, RubricAcknowledgementReason, RubricIssueDetail, RubricLevel } from "../api/types";
import { WEIGHT_IMPORTANCE_PREVIEW } from "../config/ui";
import { useRouteFocus } from "../routing/useRouteFocus";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { type CriterionEdit, useRubric, useSaveCriteria } from "./useRubric";
import { UploadRubricModal } from "./UploadRubricModal";
import { FormatReviewWorkbench } from "./FormatReviewWorkbench";
import { CriterionLevels, ReviewItemNavigator, RubricReviewStatus, SourceReadingSummary } from "./RubricReviewParts";
import { issueCopy, levelAnchorId, openIssues, orderedIssues, tidy, type PendingAcknowledgement } from "./rubricReview";

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
}

function CriterionCard({
  row,
  index,
  average,
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
        <p><strong>Relative weight: </strong>{row.weight} · {importance(row.weight, average)} importance</p>
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
  const [blocked, setBlocked] = useState<{ kind: "activate" | "save"; unknown: number } | null>(null);
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

  useEffect(() => {
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
    const next = rubric.criteria.map((criterion) => ({
      key: String(criterion.id),
      id: criterion.id,
      type: criterion.type,
      text: criterion.text,
      evidence: criterion.evidence ?? "",
      weight: criterion.weight,
      levels: criterion.levels ?? null,
    }));
    setRows(next);
    setSavedRows(next);
    setLoadedRevision(rubric.revision ?? null);
  }, [rows, rubric]);

  const readOnly = rubric ? !rubric.is_latest_version || rubric.can_edit_criteria === false : true;
  const dirty = !readOnly && rows !== undefined && savedRows !== undefined
    && (JSON.stringify(rows) !== JSON.stringify(savedRows) || pending.size > 0);
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
  useEffect(() => {
    if (!blocked) { focusBlockedSummary.current = false; return; }
    if (!focusBlockedSummary.current || !blockedRef.current) return;
    focusBlockedSummary.current = false;
    blockedRef.current.scrollIntoView({ block: "nearest" });
    blockedRef.current.focus({ preventScroll: true });
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
  const details = rubric.parse_issue_details ?? null;
  // Issues on a criterion removed on this page are settled by the next save.
  const pageIssues = details
    ? orderedIssues(details, currentRows.map((row) => row.id))
      .filter((issue) => issue.criterion_id === null || currentRows.some((row) => row.id === issue.criterion_id))
    : [];
  const openItems = openIssues(pageIssues, pending);
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
    return tidy(row.text) || `Criterion ${index + 1}`;
  }

  function issuePlace(issue: RubricIssueDetail): { label: string; anchor: string } {
    const index = currentRows.findIndex((row) => row.id !== null && row.id === issue.criterion_id);
    const row = index >= 0 ? currentRows[index] : undefined;
    const level = issue.level === null ? undefined : row?.levels?.find((item) => item.level === issue.level);
    if (!row) return { label: "The whole format", anchor: levelAnchorId("format", null) };
    return { label: `${criterionName(row, index)}${level ? ` · ${level.name}` : ""}`, anchor: levelAnchorId(row.key, level ? level.level : null) };
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
    announce(position >= 0 ? `Item ${position + 1} of ${openItems.length}: ${place.label}.` : `${place.label}.`);
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
    announce("Wording kept, not saved yet.");
  }

  function undoKeep(issues: readonly RubricIssueDetail[]) {
    setPending((current) => {
      const next = new Map(current);
      for (const issue of issues) next.delete(issue.id);
      return next;
    });
    announce("Kept wording undone.");
  }

  const levelReview = (row: CriterionRow, index: number): LevelReview => ({
    name: criterionName(row, index), levels: freshLevels(row), issues: issuesFor(row), pending, nextAfter, onGo: goToIssue,
    onKeep: keepIssues, onUndoKeep: undoKeep,
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
      const unknown = ids.filter((id) => !pageIssues.some((issue) => issue.id === id)).length;
      setBlocked({ kind: "save", unknown });
      announce("Nothing was saved. Check these items first.");
      void refetch();
      return;
    }
    setSavedRows(submitted);
    setRemovedStack([]);
    setPending(new Map());
    setBlocked({ kind: "activate", unknown: 0 });
    announce("Your changes were saved. The format was not activated.");
    if (revision !== null) setLoadedRevision(revision);
    void refetch().then((result) => { if (revision === null && result.data) setLoadedRevision(result.data.revision ?? null); });
  }

  // Any save of an active format, and any activation, waits until every item
  // is kept or settled. The server refuses it too; this saves a round trip.
  function blockedBeforeSaving(confirm: boolean): boolean {
    if (!openItems.length || !(rubric!.is_active || confirm)) return false;
    focusBlockedSummary.current = true;
    setBlocked({ kind: rubric!.is_active ? "save" : "activate", unknown: 0 });
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

  function savedAnnouncement(out: { parse_issue_details?: RubricIssueDetail[] | null }, confirm: boolean) {
    if (confirm) return "Format confirmed and activated.";
    if (!out.parse_issue_details) return "Draft saved.";
    const left = out.parse_issue_details.filter((issue) => issue.status === "open").length;
    return left ? `Draft saved. ${left} ${left === 1 ? "item" : "items"} to check remain.` : "Draft saved.";
  }

  function discardAndReload() {
    setDiscardOpen(false);
    save.reset();
    void refetch().then((result) => {
      // Reload only from a fresh read, never from the cached copy.
      if (!result.isSuccess) return;
      setPending(new Map());
      setRemovedStack([]);
      setBlocked(null);
      setAttempted(undefined);
      setRows(undefined);
      setSavedRows(undefined);
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
    const equal = Math.round((100 / currentRows.length) * 1000) / 1000;
    setRows((current) => current?.map((row) => ({ ...row, weight: equal })));
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
    save.mutate(
      bodyFor(submitted, confirm),
      {
        onSuccess: (out) => {
          setSavedRows(submitted);
          setRemovedStack([]);
          setPending(new Map());
          setLoadedRevision(out.revision ?? null);
          announce(savedAnnouncement(out, confirm));
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === "unresolved_rubric_issues") refusedForOpenItems(error, submitted);
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
          if (error instanceof ApiError && error.status === 422) { focusRefusalAfterDialog.current = true; resolveBlocker("keep"); }
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
  const saveError = conflict ? undefined : save.error instanceof ApiError && save.error.status === 422
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
        {(checked || legacy) && <RubricReviewStatus total={pageIssues.length} open={openItems.length} readOnly={readOnly}
          isActive={rubric.is_active} legacy={legacy} onFirst={() => openItems[0] && goToIssue(openItems[0])} />}
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
        {blocked && openItems.length + blocked.unknown > 0 && (
          <div ref={blockedRef} tabIndex={-1} role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>{blocked.kind === "save" ? "Check these items before saving changes to this active format" : "Check these items before activating"}</h2>
            {blocked.kind === "save" && <p>Nothing was saved. Your edits are still on this page.</p>}
            <ul>{openItems.map((issue) => <li key={issue.id}><button type="button" onClick={() => goToIssue(issue)}>
              {issuePlace(issue).label}: {issueCopy(issue)}</button></li>)}</ul>
            {blocked.unknown > 0 && <p>Saving these changes would also leave {blocked.unknown === 1 ? "one level description" : `${blocked.unknown} level descriptions`} that no longer {blocked.unknown === 1 ? "matches" : "match"} the document. Change {blocked.unknown === 1 ? "it" : "them"} back before saving.</p>}
          </div>
        )}
        {formatIssues.length > 0 && <section className="signal-format-issues" aria-labelledby="format-issues-heading">
          <h3 id="format-issues-heading">Items about the whole format</h3>
          <CriterionLevels criterionKey="format" criterionName="The whole format" levels={[]} issues={formatIssues} pending={pending}
            readOnly={readOnly} onKeep={keepIssues} onUndoKeep={undoKeep} onPageShown={() => {}} nextAfter={nextAfter} onGo={goToIssue}
            rowHeading="From reading the format" />
        </section>}
        {problems.length > 0 && (
          <div ref={summaryRef} tabIndex={-1} role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>Fix these items before {attempted === "confirm" ? "confirming" : "saving"}</h2>
            <ul>{problems.map((problem) => <li key={problem.key}><button type="button" onClick={() => { if ("targetId" in problem && problem.targetId) document.getElementById(problem.targetId)?.focus(); else addRef.current?.focus(); }}>{problem.message}</button></li>)}</ul>
          </div>
        )}
      </div>
      {pageIssues.length > 0 && <ReviewItemNavigator items={openItems} currentId={currentIssueId} onGo={goToIssue} />}
      {currentRows.length > 0 ? (
        <ol className="signal-criteria-list" aria-label="Rubric criteria">
          {currentRows.map((row, index) => (
            <CriterionCard
              key={row.key}
              row={row}
              index={index}
              average={average}
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
              {details !== null && <p>You can save a draft at any time. Activation waits until each item is checked and kept, or no longer applies.</p>}
            </div>
            <div>
              <Button
                variant="secondary"
                aria-busy={save.isPending || undefined}
                aria-disabled={save.isPending || undefined}
                onClick={() => attemptSave(false)}
              >
                Save draft
              </Button>
              <Button
                variant="brand"
                aria-busy={save.isPending || undefined}
                aria-disabled={save.isPending || undefined}
                data-tour="confirm-rubric-cta"
                onClick={() => attemptSave(true)}
              >
                Confirm and activate format
              </Button>
            </div>
          </div>
        )}
        {save.isPending && <p role="status">Saving criteria…</p>}
        {saveError && <div ref={saveErrorRef} tabIndex={-1}><Alert title="Could not save these criteria" tone="error" role="alert">{saveError}{refusedList}</Alert></div>}
        <div ref={conflictRef} tabIndex={-1}>
        {conflict === "rubric_changed" && <Alert title="This format changed somewhere else" tone="error" role="alert">
          <p>This format changed in another tab or window. Your edits are still on this page but were not saved.</p>
          <Button variant="secondary" onClick={() => setDiscardOpen(true)}>Load the latest version</Button>
        </Alert>}
        {conflict === "rubric_locked" && <Alert title="This version is locked" tone="error" role="alert">
          <p>A check started using this version, so it is now locked. Your edits were not saved.</p>
          <p>Upload a new version to make corrections. Existing reports keep their original criteria.</p>
          <Button variant="secondary" onClick={() => setUploadOpen(true)}>Upload new version</Button>
        </Alert>}
        </div>
        {rubric.is_active && <Alert title="This format is active" tone="success">New checks use this version.</Alert>}
      </div>
      </FormatReviewWorkbench>
      {blocker.state === "blocked" && (
        <Dialog
          title="Leave without saving your changes?"
          onClose={() => resolveBlocker("keep")}
          actions={<><Button variant="secondary" disabled={save.isPending} onClick={() => resolveBlocker("keep")}>Keep editing</Button><Button variant="danger" disabled={save.isPending} onClick={() => resolveBlocker("leave")}>Leave without saving</Button><Button variant="brand" busy={save.isPending} onClick={saveAndLeave}>Save draft and leave</Button></>}
        >
          <div className="signal-dialog-copy"><p>Your unsaved criteria changes will be discarded if you leave now.</p>{leaveSaveFailed && saveError && <Alert title="Could not save" tone="error" role="alert">{saveError}</Alert>}</div>
        </Dialog>
      )}
      {discardOpen && (
        <Dialog
          title="Discard your edits and load the latest version?"
          onClose={() => setDiscardOpen(false)}
          actions={<><Button variant="secondary" onClick={() => setDiscardOpen(false)}>Keep editing</Button><Button variant="danger" onClick={discardAndReload}>Discard and load</Button></>}
        >
          <div className="signal-dialog-copy"><p>Your unsaved changes on this page, including wording you chose to keep, will be replaced by the version saved elsewhere.</p></div>
        </Dialog>
      )}
      {uploadOpen && <UploadRubricModal familyId={rubric.rubric_family_id} onClose={() => setUploadOpen(false)} />}
    </div>
  );
}
