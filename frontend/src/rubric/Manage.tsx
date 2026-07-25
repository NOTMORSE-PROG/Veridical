// Screen 4m — Rubric management: versions (F2.4). A version list per
// family: activate any version, view any version's criteria (read-only
// for superseded ones, ReviewCriteria.tsx), delete a version blocked
// when reports are pinned to it. F2.5 (criterion library import) is
// proposed-only in FEATURES.md — not built here.
//
// Honest scope note: a multi-family switcher isn't built — this shows
// the FIRST family only. Two families coexisting is proven at the data/
// API layer (list_rubric_families returns one row per family, tested),
// but nothing in this screen lets an instructor switch between them yet.
import { useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import {
  useActivateRubric,
  useDeleteRubric,
  useRubricFamilies,
  useRubricVersions,
} from "./useRubric";
import { UploadRubricModal } from "./UploadRubricModal";

export function ManageRubricPage() {
  const { data: families, isPending } = useRubricFamilies();
  const family = families?.[0];
  const { data: versions } = useRubricVersions(family?.rubric_family_id);
  const activate = useActivateRubric();
  const del = useDeleteRubric();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (isPending) {
    return <div className="p-8 text-xs text-ink-faint">Loading…</div>;
  }
  if (!family) {
    return (
      <div className="p-8 text-xs text-ink-faint">
        No required format uploaded yet — go to the Dashboard to upload one.
      </div>
    );
  }

  const active = versions?.find((v) => v.is_active) ?? family;

  async function handleDelete(id: number) {
    setActionError(null);
    try {
      await del.mutateAsync(id);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Delete failed. Try again.");
    }
  }

  async function handleActivate(id: number) {
    setActionError(null);
    try {
      await activate.mutateAsync(id);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Activate failed. Try again.");
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2.5 rounded-panel border border-border bg-panel px-3.5 py-2.5 text-xs">
        <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
          Active format
        </span>
        <b className="text-ink">{active.title}</b>
        <span className="inline-flex items-center rounded-pill bg-status-ok-bg px-2.5 py-0.5 text-xs font-semibold text-status-ok-text">
          v{active.version} · Active
        </span>
        <span className="text-ink-faint">{active.criteria_count} criteria</span>
        <span className="flex-1" />
        <Link
          to={`/rubric/${active.id}/review`}
          className="rounded-control border border-border-button bg-panel px-2.5 py-1 text-xs text-ink"
        >
          View criteria
        </Link>
        <button
          type="button"
          onClick={() => setUploadOpen(true)}
          className="rounded-control border border-primary bg-primary px-2.5 py-1 text-xs font-medium text-on-primary"
        >
          Upload new format
        </button>
      </div>

      <div className="overflow-hidden rounded-panel border border-border">
        <div className="grid grid-cols-[56px_1fr_74px_100px_90px_auto] gap-2.5 bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header text-table-head uppercase">
          <span>Version</span>
          <span>File</span>
          <span>Criteria</span>
          <span>Uploaded</span>
          <span>Reports</span>
          <span />
        </div>
        {versions?.map((version) => (
          <div
            key={version.id}
            className="grid grid-cols-[56px_1fr_74px_100px_90px_auto] items-center gap-2.5 border-t border-border-soft px-3.5 py-2 text-xs"
          >
            <b>v{version.version}</b>
            <span>{version.title}</span>
            <span>{version.criteria_count}</span>
            <span className="text-ink-faint">
              {new Date(version.created_at).toLocaleDateString()}
            </span>
            <span>
              {version.report_count}
              {version.report_count > 0 && <span className="ml-1 text-ink-faint">pinned</span>}
            </span>
            <span className="flex items-center justify-end gap-2.5">
              <Link to={`/rubric/${version.id}/review`} className="text-primary hover:underline">
                View
              </Link>
              {version.is_active ? (
                <span className="inline-flex items-center rounded-pill bg-status-ok-bg px-2 py-0.5 text-2xs font-semibold text-status-ok-text">
                  Active
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => handleActivate(version.id)}
                  className="text-primary hover:underline"
                >
                  Activate
                </button>
              )}
              {!version.is_active && (
                <button
                  type="button"
                  onClick={() => handleDelete(version.id)}
                  title={
                    version.report_count > 0
                      ? "Has reports pinned to it — can't be deleted"
                      : undefined
                  }
                  className="text-status-bad-text hover:underline"
                >
                  Delete
                </button>
              )}
            </span>
          </div>
        ))}
      </div>

      {actionError && (
        <p role="alert" className="text-xs font-medium text-status-bad-text">
          {actionError}
        </p>
      )}
      <p className="text-xs text-ink-faint">
        — a rubric change is a measurement change: old reports stay pinned to their version for
        comparability
      </p>

      {uploadOpen && (
        <UploadRubricModal
          onClose={() => setUploadOpen(false)}
          familyId={family.rubric_family_id}
        />
      )}
    </div>
  );
}
