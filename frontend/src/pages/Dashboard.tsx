// Screens 4b (empty) / 4e (populated) — Dashboard (F8.8 first slice,
// F9.2). Empty-state (4b) shows until a rubric is confirmed & active
// (V-012); once one exists, 4e's KPI cards + manuscripts table take over
// (V-021). The quota meter (V-009) lives in AppShell's top bar for every
// screen, so it isn't duplicated here.
import { useMemo, useRef, useState } from "react";
import { useDismissOnboarding, useMe } from "../auth/useAuth";
import { NewCheckModal } from "../check/NewCheck";
import { KpiCards } from "../dashboard/KpiCards";
import { ManuscriptsTable } from "../dashboard/ManuscriptsTable";
import { OnboardingBanner } from "../dashboard/OnboardingBanner";
import { useDashboardStats } from "../dashboard/useDashboard";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useRubricFamilies } from "../rubric/useRubric";
import { UploadRubricModal } from "../rubric/UploadRubricModal";

function EmptyState({
  onUpload,
  showOnboarding,
  onDismissOnboarding,
}: {
  onUpload: () => void;
  showOnboarding: boolean;
  onDismissOnboarding: () => void;
}) {
  const panelHeadingRef = useRef<HTMLHeadingElement>(null);

  function handleBannerDismiss() {
    onDismissOnboarding();
    // The banner unmounts on dismiss, so focus must move to the next
    // logical heading rather than fall back to <body> -- the SPA-focus
    // rule applied at component-transition scale, not just route scale.
    requestAnimationFrame(() => panelHeadingRef.current?.focus());
  }

  return (
    <>
      {showOnboarding && <OnboardingBanner onDismiss={handleBannerDismiss} />}
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
          onClick={() => {
            onUpload();
            onDismissOnboarding(); // starting the flow is an implicit "got it"
          }}
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
        "New check" stays disabled until a rubric is active.
      </p>
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
  const { data: families, isPending: familiesPending } = useRubricFamilies();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [newCheckOpen, setNewCheckOpen] = useState(false);
  const [onboardingHiddenLocally, setOnboardingHiddenLocally] = useState(false);
  const dismissOnboarding = useDismissOnboarding();
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Dashboard - VERIDICAL", headingRef);

  // "New check" (screen 4f) only makes sense once at least one rubric is
  // confirmed & active — same precondition that switches the whole
  // screen from the empty state (4b) to the populated one (4e).
  const hasActiveRubric = useMemo(() => (families ?? []).some((f) => f.is_active), [families]);

  // Persisted server-side (Instructor.onboarding_dismissed_at) — never
  // localStorage/sessionStorage, so it survives logout and a different
  // browser. `me` withheld (undefined) until resolved avoids a flash of
  // the banner before the real flag is known.
  const showOnboarding = Boolean(me) && !me?.onboarding_dismissed_at && !onboardingHiddenLocally;

  function handleDismissOnboarding() {
    setOnboardingHiddenLocally(true); // hide instantly; never gate on the mutation
    dismissOnboarding.mutate();
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
        <button
          type="button"
          disabled={!hasActiveRubric}
          onClick={() => setNewCheckOpen(true)}
          title={hasActiveRubric ? undefined : "Upload a required format to enable checks"}
          className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
        >
          New check
        </button>
      </div>

      {newCheckOpen && <NewCheckModal onClose={() => setNewCheckOpen(false)} />}
      {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}

      {/* Staged reveal: don't paint either state until we genuinely know
          which one is true (same pattern as 4v's LandingPending — avoids a
          flash of the wrong screen while `families` is still resolving). */}
      {familiesPending ? null : hasActiveRubric ? (
        <PopulatedDashboard />
      ) : (
        <EmptyState
          onUpload={() => setUploadOpen(true)}
          showOnboarding={showOnboarding}
          onDismissOnboarding={handleDismissOnboarding}
        />
      )}
    </div>
  );
}
