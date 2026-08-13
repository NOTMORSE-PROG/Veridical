// Top-nav shell — the ONE shell every authenticated page renders inside
// (V-014 AC: no per-page nav). V-055 reconstruction: sticky header, a real
// mobile disclosure nav (BUG-015's fix also applies here), a 3-state quota
// chip with a direction word (BUG-017), a 44px avatar hit area (BUG-019).
//
// Nav redesign (V-055, owner-requested follow-up): fixed BUG-024 (the
// header overflowed the WCAG 1.4.10 320px reflow floor by 24px — measured
// element-by-element, the fixed-width chrome alone exceeded 320px before
// the flex-1 spacer had anything left to absorb) and removed the three
// permanently-disabled "Soon" nav items. None of GOV.UK/USWDS/Material 3's
// own primary-nav patterns document a disabled-nav-item component; primary
// nav in each system is scoped to destinations that exist. "Submissions"
// specifically referenced the V7 student portal, which is PROPOSED and
// BLOCKED pending adviser approval (D-005) -- a permanent nav item for it
// was advertising an unapproved feature in the one piece of chrome every
// screen renders, not just an unbuilt-but-committed one (Archive/Settings,
// V-042, are a materially different case). Desktop/mobile now switch at a
// single `md:` (768px) breakpoint (was two staggered ones, `sm:`/`lg:`) --
// re-measured live: the 3-real-item nav + full quota sentence needs
// ~686-690px, `md:` leaves ~78-82px of margin. Account/sign-out moved out
// of the persistent header row into the mobile disclosure panel (GOV.UK/
// USWDS both separate "primary navigation" from "utility/account actions"
// into distinct regions) -- the desktop avatar is unchanged, just now
// `md:`-gated alongside the rest.
import { type FocusEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { useQuota } from "../api/useQuota";
import { useLogout, useMe } from "../auth/useAuth";
import { CoachMark } from "../onboarding/CoachMark";
import { TourProvider, useTour } from "../onboarding/TourContext";

const NAV_ITEMS: ReadonlyArray<{ label: string; to: string }> = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "Rubric", to: "/rubric" },
  { label: "Audit log", to: "/audit" },
];

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function DesktopNavLinks() {
  const location = useLocation();
  return (
    <>
      {NAV_ITEMS.map((item) => {
        const isActive = location.pathname === item.to;
        return (
          <Link
            key={item.label}
            to={item.to}
            aria-current={isActive ? "page" : undefined}
            className="flex items-center border-b-2 border-transparent py-2 text-sm text-ink-secondary hover:text-ink aria-[current=page]:border-action aria-[current=page]:font-semibold aria-[current=page]:text-ink"
          >
            {item.label}
          </Link>
        );
      })}
    </>
  );
}

function MobileNavLinks({ onNavigate }: { onNavigate: () => void }) {
  const location = useLocation();
  return (
    <>
      {NAV_ITEMS.map((item) => {
        const isActive = location.pathname === item.to;
        return (
          <Link
            key={item.label}
            to={item.to}
            onClick={onNavigate}
            aria-current={isActive ? "page" : undefined}
            className="flex min-h-11 items-center rounded-md border-l-2 border-transparent px-3 text-sm text-ink-secondary hover:bg-status-neutral-bg hover:text-ink aria-[current=page]:border-action aria-[current=page]:bg-status-neutral-bg aria-[current=page]:font-semibold aria-[current=page]:text-ink"
          >
            {item.label}
          </Link>
        );
      })}
    </>
  );
}

function SignOutIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
      <path d="M4 12h12" />
      <path d="M12 7l5 5-5 5" />
    </svg>
  );
}

// Reuses the exact spinner-arrow path already drawn identically in
// Report.tsx/UploadRubricModal.tsx/NewCheck.tsx/ReviewCriteria.tsx/
// StatusPill.tsx, minus the animate-spin class — a static circular
// arrow, zero new asset, already visually familiar in this app as
// "refresh/replay" from every one of those loading states (V-057).
function ReplayIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function ReplayTourButton({
  variant,
  onNavigate,
}: {
  variant: "desktop" | "mobile";
  onNavigate?: () => void;
}) {
  const { data: me } = useMe();
  // Goes through the SAME useTour() instance CoachMark reads (shared via
  // TourProvider, wrapped around this whole shell below) -- not its own
  // useReplayOnboarding() mutation. Two independent instances each had
  // their own local "was just dismissed" flag; clearing the server field
  // through one instance's mutation never reset the OTHER instance's
  // flag, so a second-or-later Replay click in one session silently did
  // nothing (found live, ux-critic). One shared instance is the fix.
  const tour = useTour();
  if (!me) return null;

  function handleReplay() {
    tour.replay();
    onNavigate?.();
  }

  if (variant === "desktop") {
    return (
      <button
        type="button"
        data-tour="replay-tour-desktop"
        onClick={handleReplay}
        aria-label="Replay VERIDICAL tour"
        className="hidden h-11 w-11 flex-none items-center justify-center rounded-full text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink md:flex"
      >
        <ReplayIcon />
      </button>
    );
  }
  return (
    <button
      type="button"
      data-tour="replay-tour-mobile"
      onClick={handleReplay}
      className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg"
    >
      <ReplayIcon />
      Replay tour
    </button>
  );
}

function MobileAccountBlock({ onNavigate }: { onNavigate: () => void }) {
  const { data: me } = useMe();
  const logout = useLogout();
  if (!me) return null;
  return (
    <div className="border-t border-border p-2">
      <p className="min-w-0 truncate px-3 py-2 text-xs text-ink-tertiary" title={me.display_name}>
        Signed in as {me.display_name}
      </p>
      <ReplayTourButton variant="mobile" onNavigate={onNavigate} />
      <button
        type="button"
        onClick={() => {
          logout.mutate();
          onNavigate();
        }}
        className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg"
      >
        <SignOutIcon />
        Sign out
      </button>
    </div>
  );
}

function QuotaChip() {
  const { data: quota, isPending, isError } = useQuota();

  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-tertiary">
        <span aria-hidden="true" className="h-3 w-8 animate-pulse rounded-full bg-neutral-150 md:w-20" />
        <span className="sr-only">Loading AI capacity</span>
      </span>
    );
  }
  if (isError || !quota) {
    return (
      <span className="inline-flex items-center rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-tertiary">
        <span aria-hidden="true" className="md:hidden">
          N/A
        </span>
        <span aria-hidden="true" className="hidden md:inline">
          AI capacity unavailable
        </span>
        <span className="sr-only">AI capacity unavailable</span>
      </span>
    );
  }
  const remainingPct =
    quota.daily_limit > 0
      ? Math.max(0, Math.round(100 - (quota.calls_used / quota.daily_limit) * 100))
      : 100;
  const atZero = remainingPct === 0;
  // Three-tier tone, same tone family the rest of the app uses for
  // "system/process state" (V-056): quota is a hard operational
  // constraint central to this product's economics (ground rule 2) but
  // previously rendered as quiet text, indistinguishable in
  // weight from decorative chrome — Nielsen's visibility-of-system-status
  // heuristic. Now a compact filled meter, not just a number.
  const tone: "neutral" | "caution" | "danger" =
    remainingPct < 15 ? "danger" : remainingPct < 40 ? "caution" : "neutral";
  // Full sentence at md:+ (matches the nav's own breakpoint below); a
  // short-but-still-directional badge below it (never a bare number,
  // BUG-017) since the full sentence doesn't fit the header once the
  // desktop nav is hidden and the hamburger is the only other content.
  const shortText = atZero ? "0% left" : `${remainingPct}% left`;
  const longText = atZero
    ? "No AI capacity left today"
    : `AI capacity: ${remainingPct}% remaining today`;
  const fillColor = `var(--color-status-${tone}-text)`;
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-border bg-panel py-1 pr-2.5 pl-1.5 text-xs whitespace-nowrap"
      style={{ color: tone === "neutral" ? undefined : fillColor }}
    >
      <span
        aria-hidden="true"
        className="h-1.5 w-10 flex-none overflow-hidden rounded-full md:w-14"
        style={{ backgroundColor: "var(--color-neutral-150)" }}
      >
        <span
          className="block h-full rounded-full"
          style={{ width: `${remainingPct}%`, backgroundColor: fillColor }}
        />
      </span>
      <span aria-hidden="true" className="md:hidden">
        {shortText}
      </span>
      <span aria-hidden="true" className="hidden md:inline">
        {longText}
      </span>
      <span className="sr-only">{longText}</span>
    </span>
  );
}

function AvatarButton() {
  const { data: me } = useMe();
  const logout = useLogout();
  if (!me) return null;
  return (
    <button
      type="button"
      onClick={() => logout.mutate()}
      aria-label={`Signed in as ${me.display_name}. Sign out`}
      className="hidden h-11 w-11 flex-none items-center justify-center rounded-full hover:bg-status-neutral-bg md:flex"
    >
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-status-neutral-bg text-xs font-semibold text-ink">
        {initials(me.display_name)}
      </span>
    </button>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const hamburgerRef = useRef<HTMLButtonElement>(null);
  const location = useLocation();

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
        hamburgerRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen]);

  // Found live (ux-critic): tabbing past the panel's last item (Sign out)
  // moved focus into <main> while the panel stayed open and opaque on top
  // of it -- a focused control the user couldn't see or reach with a click
  // (WCAG 2.4.11 Focus Not Obscured). This is a disclosure, not a modal
  // (Modal.tsx's real focus trap is reserved for actual dialogs), so the
  // fix is to close automatically when focus leaves the panel, not to trap
  // it -- the same pattern GOV.UK's own mobile nav uses. Exempts the
  // hamburger itself so Shift+Tab back to the trigger doesn't snap it shut.
  function handlePanelBlur(event: FocusEvent<HTMLDivElement>) {
    const next = event.relatedTarget as Node | null;
    if (next && (event.currentTarget.contains(next) || next === hamburgerRef.current)) return;
    setMobileNavOpen(false);
  }

  return (
    // TourProvider wraps the whole shell (not just CoachMark) so the
    // header's ReplayTourButton and CoachMark itself read the exact
    // same useTour() instance -- one source of truth for "is the tour
    // showing," not two independently-instantiated ones (V-057 fix).
    <TourProvider>
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b-[3px] border-accent bg-panel px-4 shadow-sm md:h-16 md:px-6 relative">
        <Link to="/dashboard" className="flex flex-none items-center gap-2 rounded-sm">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 flex-none items-center justify-center rounded-sm bg-action text-sm font-bold text-on-action"
          >
            V
          </span>
          <span className="text-sm font-bold tracking-header text-ink">VERIDICAL</span>
        </Link>

        <nav aria-label="Primary" className="ml-2.5 hidden items-center gap-4 md:flex">
          <DesktopNavLinks />
        </nav>

        <span className="flex-1" />

        <QuotaChip />

        <ReplayTourButton variant="desktop" />

        <button
          ref={hamburgerRef}
          type="button"
          onClick={() => setMobileNavOpen((v) => !v)}
          aria-expanded={mobileNavOpen}
          aria-controls="mobile-nav-panel"
          aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
          className="flex h-11 w-11 flex-none items-center justify-center text-ink md:hidden"
        >
          {mobileNavOpen ? (
            <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="5" y1="5" x2="19" y2="19" />
              <line x1="19" y1="5" x2="5" y2="19" />
            </svg>
          ) : (
            <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="4" y1="7" x2="20" y2="7" />
              <line x1="4" y1="12" x2="20" y2="12" />
              <line x1="4" y1="17" x2="20" y2="17" />
            </svg>
          )}
        </button>

        {/*
         * DOM-positioned right after the hamburger so Tab order is
         * hamburger -> nav links -> account block, matching visual intent.
         * `absolute` keeps it visually full-width below the header
         * regardless of DOM position; the header is `relative` at this
         * same breakpoint so the panel anchors to it, not the page.
         */}
        <div
          id="mobile-nav-panel"
          hidden={!mobileNavOpen}
          onBlur={handlePanelBlur}
          className="absolute top-full right-0 left-0 border-b border-border bg-panel md:hidden"
        >
          <nav aria-label="Primary" className="flex flex-col gap-1 p-2">
            <MobileNavLinks onNavigate={() => setMobileNavOpen(false)} />
          </nav>
          <MobileAccountBlock onNavigate={() => setMobileNavOpen(false)} />
        </div>

        <AvatarButton />
      </header>

      {/*
       * V-056: a dimmed backdrop behind the mobile nav panel, found
       * missing during the ticket's ui-designer spec pass — page content
       * below the panel showed through undimmed. Click-to-close only,
       * deliberately NOT a focus trap: this is a disclosure that closes
       * automatically when focus leaves it (see handlePanelBlur above),
       * not a modal that blocks interaction with the rest of the page —
       * converting it to a hard focus trap would contradict the WCAG
       * 2.4.11 fix already verified live for this exact panel. The scrim
       * is a visual-clarity fix only.
       */}
      {mobileNavOpen && (
        <div
          aria-hidden="true"
          onClick={() => setMobileNavOpen(false)}
          className="fixed inset-x-0 top-14 bottom-0 z-30 bg-neutral-900/50 md:hidden"
        />
      )}

      <main id="main-content" tabIndex={-1} className="flex flex-1 flex-col">
        {children}
      </main>

      <CoachMark />
    </div>
    </TourProvider>
  );
}
