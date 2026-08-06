// First-run welcome banner (Flow A, V-055). Not a modal: this is
// non-blocking, skippable orientation content (GOV.UK Notification
// Banner / USWDS Site Alert convention), not a decision the instructor
// must resolve before proceeding -- Modal.tsx's focus trap would be the
// wrong tool here (the "component named X that isn't actually X"
// mistake, same failure class as BUG-020, just in the opposite
// direction). Persisted server-side (Instructor.onboarding_dismissed_at
// via /auth/onboarding/dismiss), never localStorage/sessionStorage, so
// it survives logout and a different browser.
//
// The dismiss mutation is owned solely by the parent (Dashboard.tsx),
// not by this component -- found live (backend-critic): calling
// useDismissOnboarding() here too fired a second, independent POST per
// click (mutations have no request-dedup the way queries do). This
// component only reports the click upward.
export function OnboardingBanner({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg bg-status-info-bg px-4 py-3.5 text-sm text-status-info-text sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-bold text-status-info-text">Welcome to VERIDICAL</h2>
        <p className="text-sm text-status-info-text">
          VERIDICAL flags things worth double-checking. It never approves or rejects a defense for
          you, and nothing runs without your confirmation at each step below.
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="flex h-11 flex-none items-center justify-center rounded-md border border-status-info-text/25 bg-panel px-4 text-sm font-bold text-status-info-text hover:bg-status-info-bg/60 sm:self-start"
      >
        Got it
      </button>
    </div>
  );
}
