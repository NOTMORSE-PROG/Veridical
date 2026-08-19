// Screens 4b (empty) / 4e (populated) — Dashboard (F8.8 first slice,
// F9.2). Empty-state (4b) shows until a rubric is confirmed & active
// (V-012); once one exists, 4e's KPI cards + manuscripts table take over
// (V-021). The quota meter (V-009) lives in AppShell's top bar for every
// screen, so it isn't duplicated here.
import { useMemo, useRef, useState } from "react";
import { useMe } from "../auth/useAuth";
import { NewCheckModal } from "../check/NewCheck";
import { UploadManuscriptModal } from "../check/UploadManuscriptModal";
import { KpiCards } from "../dashboard/KpiCards";
import { ManuscriptsTable } from "../dashboard/ManuscriptsTable";
import { useDashboardStats } from "../dashboard/useDashboard";
import { useRouteFocus } from "../routing/useRouteFocus";
import { RerunModal } from "../rubric/RerunModal";
import { useRubricFamilies } from "../rubric/useRubric";
import { UploadRubricModal } from "../rubric/UploadRubricModal";

function EmptyState({ onUpload }: { onUpload: () => void }) {
  const panelHeadingRef = useRef<HTMLHeadingElement>(null);

  return (
    <>
      <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
        <h2 ref={panelHeadingRef} tabIndex={-1} className="text-md font-bold text-ink">
          No required format yet
        </h2>
        <p className="max-w-md text-sm text-ink-secondary">
          Upload the rubric or format document (PDF or DOCX). VERIDICAL parses it into checkable
          criteria for your review. Nothing runs until you confirm.
        </p>
        <button
          type="button"
          data-tour="upload-format-cta"
          onClick={onUpload}
          className="mt-1 flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
        >
          Upload required format
        </button>
      </div>
      <ol className="flex flex-wrap items-center justify-center gap-2 text-sm" aria-label="Setup steps">
        <li className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-xs text-ink-secondary">
          <b className="font-bold text-ink">1</b>&nbsp;Upload format
        </li>
        <li aria-hidden="true" className="text-ink-tertiary">
          &rarr;
        </li>
        <li className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-xs text-ink-secondary">
          <b className="font-bold text-ink">2</b>&nbsp;Review parsed criteria
        </li>
        <li aria-hidden="true" className="text-ink-tertiary">
          &rarr;
        </li>
        <li className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-xs text-ink-secondary">
          <b className="font-bold text-ink">3</b>&nbsp;Check manuscripts
        </li>
      </ol>
      <p className="text-center text-sm text-ink-tertiary">
        Click "New check" any time. It tells you what's missing instead of just sitting there.
      </p>
    </>
  );
}

function PopulatedDashboard({
  onUploadManuscript,
  onRerun,
  onStartCheck,
}: {
  onUploadManuscript: () => void;
  onRerun: (manuscriptId: number) => void;
  onStartCheck: (manuscriptId: number) => void;
}) {
  const { data: stats } = useDashboardStats();
  const [page, setPage] = useState(1);
  // V-062 (AC5): changing the filter starts back at page 1 -- a filter
  // that silently kept you on, say, page 3 of a now much-shorter result
  // set would just as often land on an empty page as a useful one.
  const [program, setProgram] = useState<string | undefined>(undefined);
  function onProgramChange(next: string | undefined) {
    setProgram(next);
    setPage(1);
  }
  return (
    <>
      {stats && <KpiCards stats={stats} />}
      <ManuscriptsTable
        page={page}
        onPageChange={setPage}
        program={program}
        onProgramChange={onProgramChange}
        onUploadManuscript={onUploadManuscript}
        onRerun={onRerun}
        onStartCheck={onStartCheck}
      />
    </>
  );
}

export function DashboardPage() {
  const { data: me } = useMe();
  const { data: families, isPending: familiesPending } = useRubricFamilies();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadManuscriptOpen, setUploadManuscriptOpen] = useState(false);
  const [newCheckOpen, setNewCheckOpen] = useState(false);
  const [preselectManuscriptId, setPreselectManuscriptId] = useState<number | undefined>(undefined);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunInitialIds, setRerunInitialIds] = useState<number[] | undefined>(undefined);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Dashboard - VERIDICAL", headingRef);

  // Drives which body renders (empty state 4b vs. populated 4e).
  const hasActiveRubric = useMemo(() => (families ?? []).some((f) => f.is_active), [families]);

  function openNewCheck() {
    setUploadManuscriptOpen(false);
    setNewCheckOpen(true);
  }

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
            Dashboard
          </h1>
          {me && <p className="text-sm text-ink-secondary">{me.display_name}</p>}
        </div>
        {/* BUG-023: never gate a header CTA behind a precondition and hide
            it — the destination explains what's missing instead (the New
            Check modal's own structural-blocker panel, now wired to this
            Upload manuscript CTA too, V-059). Both buttons are always
            present, consistent with that established precedent. */}
        <div className="flex flex-col gap-2 sm:flex-row">
          <button
            type="button"
            onClick={() => setUploadManuscriptOpen(true)}
            className="flex h-11 w-full items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg sm:w-auto"
          >
            Upload manuscript
          </button>
          <button
            type="button"
            data-tour="new-check-cta"
            onClick={() => setNewCheckOpen(true)}
            className="flex h-11 w-full items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg sm:w-auto"
          >
            New check
          </button>
        </div>
      </div>

      {newCheckOpen && (
        <NewCheckModal
          onClose={() => {
            setNewCheckOpen(false);
            setPreselectManuscriptId(undefined);
          }}
          initialManuscriptId={preselectManuscriptId}
          onUploadManuscript={() => {
            setNewCheckOpen(false);
            setUploadManuscriptOpen(true);
          }}
        />
      )}
      {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}
      {uploadManuscriptOpen && (
        <UploadManuscriptModal
          onClose={() => setUploadManuscriptOpen(false)}
          onUploadSuccess={(manuscriptId) => {
            setPreselectManuscriptId(manuscriptId);
            openNewCheck();
          }}
        />
      )}
      {rerunOpen && (
        <RerunModal
          onClose={() => {
            setRerunOpen(false);
            setRerunInitialIds(undefined);
          }}
          initialManuscriptIds={rerunInitialIds}
        />
      )}

      {/* Staged reveal: don't paint either state until we genuinely know
          which one is true (same pattern as 4v's LandingPending — avoids a
          flash of the wrong screen while `families` is still resolving). */}
      {familiesPending ? null : hasActiveRubric ? (
        <PopulatedDashboard
          onUploadManuscript={() => setUploadManuscriptOpen(true)}
          onRerun={(manuscriptId) => {
            setRerunInitialIds([manuscriptId]);
            setRerunOpen(true);
          }}
          onStartCheck={(manuscriptId) => {
            setPreselectManuscriptId(manuscriptId);
            openNewCheck();
          }}
        />
      ) : (
        <EmptyState onUpload={() => setUploadOpen(true)} />
      )}
    </div>
  );
}
