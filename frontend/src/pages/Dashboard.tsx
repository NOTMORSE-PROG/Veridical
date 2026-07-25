// Screen 4b — Dashboard, first-run empty state (F8, F9.2). Populated state
// (4e, KPI cards) is V-021; "Upload required format" opens the parse
// modal (4c) wired up in V-012.
import { useMe } from "../auth/useAuth";
import { Chip } from "../components/Chip";

export function DashboardPage() {
  const { data: me } = useMe();

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
          disabled
          title="Upload a required format to enable checks"
          className="rounded-control border border-border-button bg-panel px-3.5 py-1.5 text-base font-medium text-ink opacity-45"
        >
          New check
        </button>
      </div>

      <div className="flex flex-col items-center gap-2 rounded-panel border-2 border-dashed border-border-button bg-page p-7">
        <b className="text-md text-ink">No required format yet</b>
        <p className="max-w-sm text-center text-xs text-ink-faint">
          Upload the rubric or format document (PDF / DOCX). VERIDICAL parses it into checkable
          criteria for your review — nothing runs until you confirm.
        </p>
        <button
          type="button"
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
      <p className="text-xs text-ink-faint">
        — "New check" stays disabled until a rubric is active
      </p>
    </div>
  );
}
