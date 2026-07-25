// Screens 4b (empty) / 4e (populated) — Dashboard (F8.8 first slice,
// F9.2). Empty-state (4b) shows until a rubric is confirmed & active
// (V-012); once one exists, 4e's KPI cards + manuscripts table take over
// (V-021). The quota meter (V-009) already lives in AppShell's top bar
// for every screen, so it isn't duplicated here.
import { useMemo, useState } from "react";
import { useMe } from "../auth/useAuth";
import { NewCheckModal } from "../check/NewCheck";
import { Chip } from "../components/Chip";
import { KpiCards } from "../dashboard/KpiCards";
import { ManuscriptsTable } from "../dashboard/ManuscriptsTable";
import { useDashboardStats } from "../dashboard/useDashboard";
import { useRubricFamilies } from "../rubric/useRubric";
import { UploadRubricModal } from "../rubric/UploadRubricModal";

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <>
      <div className="flex flex-col items-center gap-2 rounded-panel border-2 border-dashed border-border-button bg-page p-7">
        <b className="text-md text-ink">No required format yet</b>
        <p className="max-w-sm text-center text-xs text-ink-faint">
          Upload the rubric or format document (PDF / DOCX). VERIDICAL parses it into checkable
          criteria for your review — nothing runs until you confirm.
        </p>
        <button
          type="button"
          onClick={onUpload}
          className="mt-1 rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary"
        >
          Upload required format
        </button>
      </div>
      <div className="flex items-center justify-center gap-2">
        <Chip>
          <b>1</b> Upload format
        </Chip>
        <span className="text-xs text-ink-faint">→</span>
        <Chip>
          <b>2</b> Review parsed criteria
        </Chip>
        <span className="text-xs text-ink-faint">→</span>
        <Chip>
          <b>3</b> Check manuscripts
        </Chip>
      </div>
      <p className="text-xs text-ink-faint">— "New check" stays disabled until a rubric is active</p>
    </>
  );
}

function PopulatedDashboard() {
  const { data: stats } = useDashboardStats();
  const [page, setPage] = useState(1);
  return (
    <>
      {stats && <KpiCards stats={stats} />}
      <ManuscriptsTable page={page} onPageChange={setPage} />
    </>
  );
}

export function DashboardPage() {
  const { data: me } = useMe();
  const { data: families } = useRubricFamilies();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [newCheckOpen, setNewCheckOpen] = useState(false);

  // "New check" (screen 4f) only makes sense once at least one rubric is
  // confirmed & active — same precondition that switches the whole
  // screen from the empty state (4b) to the populated one (4e).
  const hasActiveRubric = useMemo(() => (families ?? []).some((f) => f.is_active), [families]);

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2.5">
        <div>
          <div className="text-lg font-bold text-ink">Dashboard</div>
          {me && <div className="text-xs text-ink-faint">{me.display_name}</div>}
        </div>
        <span className="flex-1" />
        <button
          type="button"
          disabled={!hasActiveRubric}
          onClick={() => setNewCheckOpen(true)}
          title={hasActiveRubric ? undefined : "Upload a required format to enable checks"}
          className="rounded-control border border-border-button bg-panel px-3.5 py-1.5 text-base font-medium text-ink disabled:opacity-45"
        >
          New check
        </button>
      </div>

      {newCheckOpen && <NewCheckModal onClose={() => setNewCheckOpen(false)} />}
      {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}

      {hasActiveRubric ? (
        <PopulatedDashboard />
      ) : (
        <EmptyState onUpload={() => setUploadOpen(true)} />
      )}
    </div>
  );
}
