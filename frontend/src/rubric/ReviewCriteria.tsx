// Screen 4d — Review parsed criteria, the HITL gate (F2.3). Nothing runs
// against a rubric until the instructor confirms here — this screen IS
// the human half of the "any format" objective.
import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { ApiError } from "../api/client";
import { Chip } from "../components/Chip";
import { SegmentedToggle } from "../components/SegmentedToggle";
import type { CriterionType } from "../api/types";
import { type CriterionEdit, useRubric, useSaveCriteria } from "./useRubric";

interface Row {
  key: string;
  id: number | null;
  type: CriterionType;
  text: string;
  evidence: string;
  weight: number;
}

const TYPE_LABELS: Record<CriterionType, string> = { structural: "Structural", semantic: "Semantic" };

let newRowCounter = 0;

export function ReviewCriteriaPage() {
  const { rubricId } = useParams<{ rubricId: string }>();
  const id = Number(rubricId);
  const { data: rubric, isPending, isError } = useRubric(id);
  const save = useSaveCriteria(id);
  const [rows, setRows] = useState<Row[] | null>(null);

  // Seed local edit state once, when the rubric first loads — a
  // background refetch must not clobber in-progress edits.
  useEffect(() => {
    if (rubric && rows === null) {
      setRows(
        rubric.criteria.map((c) => ({
          key: String(c.id),
          id: c.id,
          type: c.type,
          text: c.text,
          evidence: c.evidence ?? "",
          weight: c.weight,
        })),
      );
    }
  }, [rubric, rows]);

  if (isPending || rows === null) {
    return <div className="p-8 text-xs text-ink-faint">Loading criteria…</div>;
  }
  if (isError) {
    return <div className="p-8 text-xs text-status-bad-text">Couldn't load this rubric.</div>;
  }

  function updateRow(key: string, patch: Partial<Row>) {
    setRows((current) => current?.map((r) => (r.key === key ? { ...r, ...patch } : r)) ?? current);
  }

  function deleteRow(key: string) {
    setRows((current) => current?.filter((r) => r.key !== key) ?? current);
  }

  function addRow() {
    setRows((current) => [
      ...(current ?? []),
      { key: `new-${++newRowCounter}`, id: null, type: "structural", text: "", evidence: "", weight: 0 },
    ]);
  }

  function normalizeWeights() {
    setRows((current) => {
      if (!current || current.length === 0) return current;
      const total = current.reduce((sum, r) => sum + r.weight, 0);
      if (total <= 0) {
        const equal = Math.round((100 / current.length) * 1000) / 1000;
        return current.map((r) => ({ ...r, weight: equal }));
      }
      return current.map((r) => ({ ...r, weight: Math.round((r.weight / total) * 100 * 1000) / 1000 }));
    });
  }

  // F2.4 (V-013): a version another version has superseded is history —
  // read-only, never silently mutated. A freshly uploaded, not-yet-
  // confirmed rubric is always its OWN family's only (hence latest)
  // version, so this never blocks the first-time confirm flow (V-012).
  const readOnly = !rubric.is_latest_version;

  const weightSum = rows.reduce((sum, r) => sum + r.weight, 0);
  const weightsBalanced = Math.abs(weightSum - 100) < 0.1;
  const canConfirm = !readOnly && rows.length > 0 && rows.every((r) => r.text.trim().length > 0);

  function toEdits(): CriterionEdit[] {
    return rows!.map((r) => ({
      id: r.id,
      type: r.type,
      text: r.text,
      evidence: r.evidence.trim() ? r.evidence : null,
      weight: r.weight,
    }));
  }

  function handleSave(confirm: boolean) {
    save.mutate({ criteria: toEdits(), confirm });
  }

  const saveError =
    save.error instanceof ApiError ? save.error.message : save.error ? "Save failed. Try again." : null;

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2 border-b border-border-soft pb-2 text-xs">
        <span className="text-ink-faint">Rubric /</span>
        <b className="text-ink">Review parsed criteria</b>
        <Chip>{rubric.title}</Chip>
        <span className="flex-1" />
        <Chip>{rows.length} criteria</Chip>
      </div>

      {readOnly ? (
        <p className="rounded-control bg-page px-3 py-2 text-xs text-ink-soft">
          A newer version of this format exists — this version is read-only history. Old reports
          stay pinned to it for comparability.
        </p>
      ) : (
        <p className="rounded-control bg-info-bg px-3 py-2 text-xs text-info-text">
          Nothing runs until you confirm. Re-type, re-weight, delete, or add criteria.
        </p>
      )}

      {rubric.parse_status === "needs_review" && (
        <div className="rounded-control bg-status-warn-bg px-3 py-2 text-xs text-status-warn-text">
          <b>Needs manual completion</b> — the automatic parse couldn't validate itself after
          several attempts. Review every row below before confirming.
          {rubric.parse_issues && rubric.parse_issues.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {rubric.parse_issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="max-h-[70vh] overflow-auto rounded-panel border border-border">
        <div className="grid grid-cols-[28px_1fr_170px_1fr_120px_28px] gap-2.5 border-b border-border-soft bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header text-table-head uppercase sticky top-0">
          <span>#</span>
          <span>Criterion</span>
          <span>Type</span>
          <span>Evidence</span>
          <span>Weight</span>
          <span />
        </div>
        {rows.map((row, index) => (
          <div
            key={row.key}
            className="grid grid-cols-[28px_1fr_170px_1fr_120px_28px] items-center gap-2.5 border-t border-border-soft px-3.5 py-2 text-xs"
          >
            <span className="text-ink-faint">{index + 1}</span>
            <input
              value={row.text}
              disabled={readOnly}
              onChange={(event) => updateRow(row.key, { text: event.target.value })}
              placeholder="Criterion text"
              className="rounded-control border border-border-input px-2 py-1 text-xs disabled:bg-page disabled:text-ink-soft"
            />
            <SegmentedToggle
              value={row.type}
              options={["structural", "semantic"] as const}
              labels={TYPE_LABELS}
              disabled={readOnly}
              onChange={(type) => updateRow(row.key, { type })}
            />
            <input
              value={row.evidence}
              disabled={readOnly}
              onChange={(event) => updateRow(row.key, { evidence: event.target.value })}
              placeholder="What evidence satisfies this?"
              className="rounded-control border border-border-input px-2 py-1 text-xs text-ink-soft disabled:bg-page"
            />
            <span className="flex items-center gap-1">
              <input
                type="number"
                min={0}
                step={0.1}
                value={row.weight}
                disabled={readOnly}
                onChange={(event) => updateRow(row.key, { weight: Number(event.target.value) || 0 })}
                className="w-16 rounded-control border border-border-input px-1.5 py-1 text-xs disabled:bg-page disabled:text-ink-soft"
              />
              <span className="text-ink-faint">%</span>
            </span>
            {!readOnly && (
              <button
                type="button"
                onClick={() => deleteRow(row.key)}
                aria-label={`Delete criterion ${index + 1}`}
                className="text-ink-faint hover:text-status-bad-text"
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>

      {!readOnly && (
        <div className="flex items-center gap-2">
          <button type="button" onClick={addRow} className="text-xs text-primary hover:underline">
            + Add criterion
          </button>
          <span className="flex-1" />
          <button
            type="button"
            onClick={normalizeWeights}
            className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-xs text-ink"
          >
            Normalize to 100%
          </button>
          <span
            className={
              weightsBalanced
                ? "inline-flex items-center rounded-pill border border-modal-border px-2.5 py-0.5 text-xs text-status-ok-text"
                : "inline-flex items-center rounded-pill border border-modal-border px-2.5 py-0.5 text-xs text-status-warn-text"
            }
          >
            Weights total {Math.round(weightSum * 10) / 10}%
          </span>
          <button
            type="button"
            disabled={rows.length === 0 || save.isPending}
            onClick={() => handleSave(false)}
            className="rounded-control border border-border-button bg-panel px-3.5 py-1.5 text-base font-medium text-ink disabled:opacity-45"
          >
            Save draft
          </button>
          <button
            type="button"
            disabled={!canConfirm || save.isPending}
            title={rows.length === 0 ? "Add at least one criterion before confirming" : undefined}
            onClick={() => handleSave(true)}
            className="rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary disabled:opacity-45"
          >
            Confirm &amp; activate rubric
          </button>
        </div>
      )}
      {!readOnly && rows.length === 0 && (
        <p className="text-xs text-status-bad-text">
          Add at least one criterion before saving or confirming — an empty rubric can't check
          anything.
        </p>
      )}
      {saveError && (
        <p role="alert" className="text-xs font-medium text-status-bad-text">
          {saveError}
        </p>
      )}
      {rubric.is_active && (
        <p className="text-xs text-status-ok-text">This rubric is active — checks use it now.</p>
      )}
      <p className="text-xs text-ink-faint">
        — a re-uploaded format later becomes v2; old reports stay pinned to their version
      </p>
    </div>
  );
}
