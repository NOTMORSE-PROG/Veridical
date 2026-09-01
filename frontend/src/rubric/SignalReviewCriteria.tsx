import { useEffect, useRef, useState } from "react";
import { Link, useBlocker, useParams } from "react-router";
import type { CriterionType, RubricLevel } from "../api/types";
import { WEIGHT_IMPORTANCE_PREVIEW } from "../config/ui";
import { useRouteFocus } from "../routing/useRouteFocus";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { type CriterionEdit, useRubric, useSaveCriteria } from "./useRubric";

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

function importance(weight: number, average: number): "Low" | "Medium" | "High" {
  if (average <= 0) return "Medium";
  const ratio = weight / average;
  if (ratio < WEIGHT_IMPORTANCE_PREVIEW.lowMaxRatio) return "Low";
  if (ratio >= WEIGHT_IMPORTANCE_PREVIEW.highMinRatio) return "High";
  return "Medium";
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

function CriteriaHeader({ headingRef, title, count }: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  title?: string;
  count?: number;
}) {
  return (
    <header className="signal-route-header signal-criteria-header">
      <div>
        <nav aria-label="Breadcrumb"><Link to="/rubric">Rubric Studio</Link><span aria-hidden="true">/</span><span>Criteria review</span></nav>
        <p className="signal-eyebrow">Prepare · Human review gate</p>
        <h1 ref={headingRef} tabIndex={-1}>Review prepared criteria</h1>
        {title && <p className="signal-route-header__intro">{title}{count !== undefined ? ` · ${count} ${count === 1 ? "criterion" : "criteria"}` : ""}</p>}
      </div>
    </header>
  );
}

function CriterionCard({
  row,
  index,
  average,
  readOnly,
  showErrors,
  onChange,
  onRemove,
}: {
  row: CriterionRow;
  index: number;
  average: number;
  readOnly: boolean;
  showErrors: boolean;
  onChange: (patch: Partial<CriterionRow>) => void;
  onRemove: () => void;
}) {
  const textError = showErrors && row.text.trim() === "";
  const weightError = showErrors && row.weight <= 0;
  return (
    <li className="signal-criterion-card">
      <div className="signal-criterion-card__number" aria-hidden="true">{index + 1}</div>
      <div className="signal-criterion-card__body">
        <div className="signal-criterion-card__heading">
          <p>Criterion {index + 1}</p>
          {!readOnly && <button type="button" onClick={onRemove} aria-label={`Remove criterion ${index + 1}`}>Remove</button>}
        </div>
        <label className="signal-criterion-field signal-criterion-field--wide">
          <span>Criterion text</span>
          <textarea
            rows={2}
            value={row.text}
            disabled={readOnly}
            aria-label={`Criterion ${index + 1} text`}
            aria-invalid={textError || undefined}
            aria-describedby={textError ? `criterion-text-error-${row.key}` : undefined}
            onChange={(event) => onChange({ text: event.target.value })}
          />
          {textError && <small id={`criterion-text-error-${row.key}`} className="signal-field-error">Enter this criterion's text.</small>}
        </label>
        <div className="signal-criterion-card__fields">
          <label className="signal-criterion-field">
            <span>Type</span>
            <select
              value={row.type}
              disabled={readOnly}
              aria-label={`Criterion ${index + 1} type`}
              onChange={(event) => onChange({ type: event.target.value as CriterionType })}
            >
              {Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="signal-criterion-field signal-criterion-field--grow">
            <span>Evidence expected</span>
            <textarea
              rows={2}
              value={row.evidence}
              disabled={readOnly}
              aria-label={`Criterion ${index + 1} evidence`}
              onChange={(event) => onChange({ evidence: event.target.value })}
            />
          </label>
          <label className="signal-criterion-field signal-criterion-field--weight">
            <span>Relative weight</span>
            <input
              type="text"
              inputMode="decimal"
              value={row.weight}
              disabled={readOnly}
              aria-label={`Criterion ${index + 1} weight`}
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
        {row.levels && row.levels.length > 0 && (
          <details className="signal-levels">
            <summary>Levelled criterion · {row.levels.length} levels</summary>
            <ol>{row.levels.map((level) => <li key={level.level}><strong>{level.name}</strong><span>{level.descriptor}</span></li>)}</ol>
          </details>
        )}
      </div>
    </li>
  );
}

export function SignalReviewCriteriaPage() {
  const { rubricId } = useParams<{ rubricId: string }>();
  const id = Number(rubricId);
  const { data: rubric, isPending, isError, refetch } = useRubric(id);
  const save = useSaveCriteria(id);
  const [rows, setRows] = useState<CriterionRow[]>();
  const [savedRows, setSavedRows] = useState<CriterionRow[]>();
  const [attempted, setAttempted] = useState<"save" | "confirm">();
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const resolvingBlocker = useRef(false);
  useRouteFocus("Review prepared criteria - VERIDICAL", headingRef);

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
  }, [rows, rubric]);

  const readOnly = rubric ? !rubric.is_latest_version : true;
  const dirty = !readOnly && rows !== undefined && savedRows !== undefined
    && JSON.stringify(rows) !== JSON.stringify(savedRows);
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

  useEffect(() => {
    if (blocker.state === "blocked") resolvingBlocker.current = false;
  }, [blocker.state]);

  if (isPending || (!isError && rubric && !rows)) {
    return <div className="signal-route signal-page-flow"><CriteriaHeader headingRef={headingRef} /><div className="signal-desk-loading" role="status" aria-busy="true"><span>Loading prepared criteria…</span><i /><i /></div></div>;
  }
  if (isError || !rubric || !rows) {
    return <div className="signal-route signal-page-flow"><CriteriaHeader headingRef={headingRef} /><Alert title="Could not load these criteria" tone="error" role="alert"><Button variant="secondary" onClick={() => refetch()}>Try again</Button></Alert></div>;
  }

  const currentRows = rows;
  const average = currentRows.length
    ? currentRows.reduce((total, row) => total + row.weight, 0) / currentRows.length
    : 0;
  const problems = attempted
    ? [
      ...(currentRows.length === 0 ? [{ key: "empty", message: "Add at least one criterion.", target: addRef }] : []),
      ...(attempted === "confirm" ? currentRows.flatMap((row, index) => [
        ...(row.text.trim() === "" ? [{ key: `text-${row.key}`, message: `Criterion ${index + 1} has no text.`, selector: `[aria-label="Criterion ${index + 1} text"]` }] : []),
        ...(row.weight <= 0 ? [{ key: `weight-${row.key}`, message: `Criterion ${index + 1}'s weight must be greater than zero.`, selector: `[aria-label="Criterion ${index + 1} weight"]` }] : []),
      ]) : []),
    ]
    : [];

  function updateRow(key: string, patch: Partial<CriterionRow>) {
    setRows((current) => current?.map((row) => row.key === key ? { ...row, ...patch } : row));
  }

  function addCriterion() {
    const next: CriterionRow = { key: `new-${++localCriterionId}`, id: null, type: "structural", text: "", evidence: "", weight: 0, levels: null };
    setRows((current) => [...(current ?? []), next]);
    setAnnouncement(`Criterion added. ${currentRows.length + 1} criteria total.`);
  }

  function removeCriterion(key: string) {
    setRows((current) => current?.filter((row) => row.key !== key));
    setAnnouncement("Criterion removed.");
  }

  function distributeWeights() {
    if (!currentRows.length) return;
    const equal = Math.round((100 / currentRows.length) * 1000) / 1000;
    setRows((current) => current?.map((row) => ({ ...row, weight: equal })));
    setAnnouncement("Relative weights set equally across all criteria.");
  }

  function attemptSave(confirm: boolean) {
    setAttempted(confirm ? "confirm" : "save");
    const hasProblems = currentRows.length === 0
      || (confirm && currentRows.some((row) => !row.text.trim() || row.weight <= 0));
    if (hasProblems) {
      queueMicrotask(() => summaryRef.current?.focus());
      return;
    }
    const submitted = currentRows;
    save.mutate(
      { criteria: toEdits(submitted), confirm },
      { onSuccess: () => { setSavedRows(submitted); setAnnouncement(confirm ? "Format confirmed and activated." : "Draft saved."); } },
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
    resolvingBlocker.current = true;
    const submitted = currentRows;
    save.mutate(
      { criteria: toEdits(submitted), confirm: false },
      {
        onSuccess: () => { setSavedRows(submitted); blocker.proceed(); },
        onError: () => { resolvingBlocker.current = false; },
      },
    );
  }

  const saveError = save.error instanceof Error ? save.error.message : save.error ? "Save failed. Try again." : undefined;

  return (
    <div className="signal-route signal-page-flow signal-criteria-review">
      <CriteriaHeader headingRef={headingRef} title={rubric.title} count={currentRows.length} />
      <div className="signal-section-flow signal-criteria-guidance">
        {readOnly
          ? <Alert title="Read-only version" tone="info">A newer version exists. Reports remain pinned to this history version for traceability.</Alert>
          : <Alert title="Review before anything runs" tone="info">Correct the prepared criteria, evidence, type, and relative importance. Nothing checks a manuscript until you confirm.</Alert>}
        {rubric.parse_status === "needs_review" && (
          <Alert title="Needs manual completion" tone="warning" role="status">
            <p>{rubric.is_active ? "This format was activated with parser uncertainty. Review and save any corrections." : "VERIDICAL could not confirm every criterion after several attempts. Check each one against the original format before confirming."}</p>
            {rubric.parse_issues?.length ? <ul>{rubric.parse_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
          </Alert>
        )}
        {problems.length > 0 && (
          <div ref={summaryRef} tabIndex={-1} role="alert" className="signal-error-summary signal-criteria-errors">
            <h2>Fix these items before {attempted === "confirm" ? "confirming" : "saving"}</h2>
            <ul>{problems.map((problem) => <li key={problem.key}><button type="button" onClick={() => { if ("selector" in problem && problem.selector) document.querySelector<HTMLElement>(problem.selector)?.focus(); else addRef.current?.focus(); }}>{problem.message}</button></li>)}</ul>
          </div>
        )}
      </div>
      {currentRows.length > 0 ? (
        <ol className="signal-criteria-list" aria-label="Rubric criteria">
          {currentRows.map((row, index) => (
            <CriterionCard
              key={row.key}
              row={row}
              index={index}
              average={average}
              readOnly={readOnly}
              showErrors={attempted === "confirm"}
              onChange={(patch) => updateRow(row.key, patch)}
              onRemove={() => removeCriterion(row.key)}
            />
          ))}
        </ol>
      ) : (
        <div className="signal-desk-empty"><h2>No criteria yet</h2><p>Add at least one criterion before saving.</p></div>
      )}
      <p className="signal-live-region" aria-live="polite">{announcement}</p>
      <div className="signal-copy-flow signal-criteria-save-region">
        {!readOnly && (
          <div className="signal-save-dock">
            <div>
              <Button ref={addRef} variant="quiet" onClick={addCriterion}>Add criterion</Button>
              <Button variant="secondary" onClick={distributeWeights}>Set equal relative weights</Button>
              <p>Weights are relative. VERIDICAL shows importance as Low, Medium, or High.</p>
            </div>
            <div>
              <Button variant="secondary" busy={save.isPending} onClick={() => attemptSave(false)}>Save draft</Button>
              <Button variant="brand" busy={save.isPending} data-tour="confirm-rubric-cta" onClick={() => attemptSave(true)}>Confirm and activate format</Button>
            </div>
          </div>
        )}
        {save.isPending && <p role="status">Saving criteria…</p>}
        {saveError && <Alert title="Could not save these criteria" tone="error" role="alert">{saveError}</Alert>}
        {rubric.is_active && <Alert title="This format is active" tone="success">New checks use this version.</Alert>}
      </div>
      {blocker.state === "blocked" && (
        <Dialog
          title="Leave without saving your changes?"
          onClose={() => resolveBlocker("keep")}
          actions={<><Button variant="secondary" disabled={save.isPending} onClick={() => resolveBlocker("keep")}>Keep editing</Button><Button variant="danger" disabled={save.isPending} onClick={() => resolveBlocker("leave")}>Leave without saving</Button><Button variant="brand" busy={save.isPending} onClick={saveAndLeave}>Save draft and leave</Button></>}
        >
          <div className="signal-dialog-copy"><p>Your unsaved criteria changes will be discarded if you leave now.</p>{saveError && <Alert title="Could not save" tone="error" role="alert">{saveError}</Alert>}</div>
        </Dialog>
      )}
    </div>
  );
}
