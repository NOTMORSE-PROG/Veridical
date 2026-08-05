// Top-nav shell — the ONE shell every authenticated page renders inside
// (V-014 AC: no per-page nav). V-055 reconstruction: sticky header, a real
// mobile disclosure nav (BUG-015's fix also applies here), a 3-state quota
// chip with a direction word (BUG-017), a 44px avatar hit area (BUG-019).
// The desktop nav switches on at `lg:` (1024px), not `sm:` (640px) --
// found live: the six-item nav plus the full quota sentence measured
// 1007-1009px wide, so anything narrower forced the WHOLE PAGE into
// horizontal scroll and pushed the sign-out button off-screen between
// 640-1009px. The disclosure nav below `lg:` covers that entire band.
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { useQuota } from "../api/useQuota";
import { useLogout, useMe } from "../auth/useAuth";

const NAV_ITEMS: ReadonlyArray<{ label: string; to: string | null }> = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "Rubric", to: "/rubric" },
  { label: "Submissions", to: null },
  { label: "Archive", to: null },
  { label: "Audit log", to: "/audit" },
  { label: "Settings", to: null },
];

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const location = useLocation();
  return (
    <>
      {NAV_ITEMS.map((item) => {
        if (item.to) {
          const isActive = location.pathname === item.to;
          return (
            <Link
              key={item.label}
              to={item.to}
              onClick={onNavigate}
              aria-current={isActive ? "page" : undefined}
              className="border-b-2 border-transparent pb-2.5 text-sm text-ink-secondary hover:text-ink aria-[current=page]:border-action aria-[current=page]:font-semibold aria-[current=page]:text-ink"
            >
              {item.label}
            </Link>
          );
        }
        return (
          <span
            key={item.label}
            aria-disabled="true"
            tabIndex={-1}
            className="flex cursor-default items-center gap-1.5 pb-2.5 text-sm text-neutral-500"
          >
            {item.label}
            <span
              aria-hidden="true"
              className="rounded-sm bg-status-attention-bg px-1.5 py-0.5 text-[11px] font-semibold text-status-attention-text"
            >
              Soon
            </span>
            <span className="sr-only">(not available yet)</span>
          </span>
        );
      })}
    </>
  );
}

function QuotaChip() {
  const { data: quota, isPending, isError } = useQuota();

  if (isPending) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-tertiary">
        <span aria-hidden="true" className="h-3 w-8 animate-pulse rounded-full bg-neutral-150 sm:w-20" />
        <span className="sr-only">Loading AI capacity</span>
      </span>
    );
  }
  if (isError || !quota) {
    return (
      <span className="inline-flex items-center rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-tertiary">
        <span aria-hidden="true" className="lg:hidden">
          N/A
        </span>
        <span aria-hidden="true" className="hidden lg:inline">
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
  // Full sentence at lg:+ (matches the nav's own breakpoint below); a
  // short-but-still-directional badge below it (never a bare number,
  // BUG-017) since the full sentence doesn't fit the header once the
  // desktop nav is hidden and the hamburger/avatar are the only other
  // content (measured live, 375px overflowed to 483px before this fix).
  const shortText = atZero ? "0% left" : `${remainingPct}% left`;
  const longText = atZero
    ? "No AI capacity left today"
    : `AI capacity: ${remainingPct}% remaining today`;
  return (
    <span
      className={
        atZero
          ? "inline-flex items-center rounded-full border border-status-attention-text/30 bg-status-attention-bg px-2.5 py-1 text-xs font-medium whitespace-nowrap text-status-attention-text"
          : "inline-flex items-center rounded-full border border-border bg-panel px-2.5 py-1 text-xs whitespace-nowrap text-ink-secondary"
      }
    >
      <span aria-hidden="true" className="lg:hidden">
        {shortText}
      </span>
      <span aria-hidden="true" className="hidden lg:inline">
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
      className="flex h-11 w-11 flex-none items-center justify-center rounded-full hover:bg-status-neutral-bg"
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

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b-[3px] border-accent bg-panel px-4 shadow-sm sm:h-16 sm:px-6 relative">
        <span
          aria-hidden="true"
          className="flex h-7 w-7 flex-none items-center justify-center rounded-sm bg-action text-sm font-bold text-on-action"
        >
          V
        </span>
        <span className="text-sm font-bold tracking-header text-ink">VERIDICAL</span>

        <nav aria-label="Primary" className="ml-2.5 hidden gap-4 lg:flex">
          <NavLinks />
        </nav>

        <span className="flex-1" />

        <QuotaChip />

        <button
          ref={hamburgerRef}
          type="button"
          onClick={() => setMobileNavOpen((v) => !v)}
          aria-expanded={mobileNavOpen}
          aria-controls="mobile-nav-panel"
          aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
          className="flex h-11 w-11 flex-none items-center justify-center text-ink lg:hidden"
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
         * DOM-positioned right after the hamburger (before the avatar) so
         * Tab order is hamburger -> nav links -> avatar, matching visual
         * intent -- found live: with this panel placed AFTER the header
         * (avatar included), Tab from the hamburger landed on "Sign out"
         * before reaching the just-opened nav (WCAG 2.4.3 Meaningful
         * Sequence). `absolute` keeps it visually full-width below the
         * header regardless of DOM position; the header is `relative` at
         * this same breakpoint so the panel anchors to it, not the page.
         */}
        <div
          id="mobile-nav-panel"
          hidden={!mobileNavOpen}
          className="absolute top-full right-0 left-0 border-b border-border bg-panel lg:hidden"
        >
          <nav aria-label="Primary" className="flex flex-col p-2">
            <NavLinks onNavigate={() => setMobileNavOpen(false)} />
          </nav>
        </div>

        <AvatarButton />
      </header>

      <main id="main-content" tabIndex={-1} className="flex flex-1 flex-col outline-none">
        {children}
      </main>
    </div>
  );
}
