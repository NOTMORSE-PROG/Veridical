// Screen 4e manuscripts table (F8.8 first slice). Shows PIPELINE status
// (queued/running/done/failed) — the READINESS verdict (Ready/Not
// Ready/etc.) lives on the report itself (screen 4h), opened via "Open
// report", not duplicated here.
import { Link } from "react-router";
import type { ManuscriptListItem } from "../api/types";
import { Pill, type PillStatus } from "../components/Pill";
import { useManuscriptsPage } from "./useDashboard";

const RUNNING_STATUSES = new Set([
  "queued",
  "ingesting",
  "structural",
  "semantic",
  "integrity",
  "aggregating",
]);

function statusPill(row: ManuscriptListItem): { label: string; pill: PillStatus } {
  if (row.latest_check_run_status === "done") return { label: "Checked", pill: "ok" };
  if (row.latest_check_run_status === "failed") return { label: "Check failed", pill: "bad" };
  if (row.latest_check_run_status && RUNNING_STATUSES.has(row.latest_check_run_status)) {
    return { label: "Checking", pill: "processing" };
  }
  if (row.ingest_status === "done") return { label: "Not checked yet", pill: "queued" };
  if (row.ingest_status === "failed") return { label: "Ingestion failed", pill: "bad" };
  return { label: "Ingesting", pill: "processing" };
}

export function ManuscriptsTable({ page, onPageChange }: { page: number; onPageChange: (p: number) => void }) {
  const { data, isLoading } = useManuscriptsPage(page);

  if (isLoading) {
    return <div className="p-3 text-xs text-ink-faint">Loading manuscripts…</div>;
  }
  if (!data) return null;

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-panel border border-border">
        <div className="grid grid-cols-[1fr_140px_140px_170px] gap-2.5 border-b border-border-soft bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header text-table-head uppercase">
          <span>Group</span>
          <span>Status</span>
          <span>Date</span>
          <span />
        </div>
        {data.items.map((row) => {
          const { label, pill } = statusPill(row);
          const running = row.latest_check_run_status
            ? RUNNING_STATUSES.has(row.latest_check_run_status)
            : false;
          return (
            <div
              key={row.id}
              className="grid grid-cols-[1fr_140px_140px_170px] items-center gap-2.5 border-t border-border-soft px-3.5 py-2 text-xs"
            >
              <span className="text-ink">{row.group_label}</span>
              <Pill status={pill}>{label}</Pill>
              <span className="text-ink-faint">
                {new Date(row.created_at).toLocaleDateString()}
              </span>
              <div className="flex items-center gap-2">
                {row.latest_check_run_status === "done" && row.latest_check_run_id && (
                  <Link
                    to={`/report/${row.latest_check_run_id}`}
                    className="text-primary hover:underline"
                  >
                    Open report
                  </Link>
                )}
                {running && row.latest_check_run_id && (
                  <Link
                    to={`/checks/${row.latest_check_run_id}`}
                    className="text-primary hover:underline"
                  >
                    View progress
                  </Link>
                )}
                <button
                  type="button"
                  disabled
                  title="Re-run against a new rubric version arrives later"
                  className="text-ink-faint opacity-45"
                >
                  Re-run
                </button>
              </div>
            </div>
          );
        })}
        {data.items.length === 0 && (
          <p className="px-3.5 py-3 text-xs text-ink-faint">No manuscripts uploaded yet.</p>
        )}
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="rounded-control border border-border-button bg-panel px-2.5 py-1 disabled:opacity-45"
          >
            Previous
          </button>
          <span className="text-ink-faint">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
            className="rounded-control border border-border-button bg-panel px-2.5 py-1 disabled:opacity-45"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
