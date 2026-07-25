// Top-nav shell — DESIGN.md §2 (wireframe .ptb/.pnav/.pmark/.pav), the ONE
// shell every authenticated page renders inside (V-014 AC: no per-page nav).
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useQuota } from "../api/useQuota";
import { useLogout, useMe } from "../auth/useAuth";
import { Chip } from "../components/Chip";
import { cx } from "../components/cx";

// "Dashboard" and "Rubric" route now; the rest are future tickets (V7
// Submissions, V-024 Audit log, V-042 Archive/Settings) — rendered
// inactive rather than linking to a page that doesn't exist.
const NAV_ITEMS: ReadonlyArray<{ label: string; to: string | null }> = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "Rubric", to: "/rubric" },
  { label: "Submissions", to: null },
  { label: "Archive", to: null },
  { label: "Audit log", to: null },
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

export function AppShell({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const { data: quota } = useQuota();
  const logout = useLogout();
  const location = useLocation();

  const quotaPct =
    quota && quota.daily_limit > 0 ? Math.round((quota.calls_used / quota.daily_limit) * 100) : 0;

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <header className="flex items-center gap-3 border-b border-border bg-panel px-4 py-2.5">
        <span className="flex h-[18px] w-[18px] flex-none items-center justify-center rounded-tag bg-primary text-2xs font-bold text-on-primary">
          V
        </span>
        <span className="text-xs font-bold tracking-header">VERIDICAL</span>
        <nav className="ml-2.5 flex gap-4 text-xs">
          {NAV_ITEMS.map((item) =>
            item.to ? (
              <Link
                key={item.label}
                to={item.to}
                className={cx(
                  "-mb-2.5 border-b-2 border-transparent pb-2.5 text-ink-soft",
                  location.pathname === item.to && "border-primary font-semibold text-primary-hover",
                )}
              >
                {item.label}
              </Link>
            ) : (
              <span key={item.label} className="cursor-default text-ink-soft opacity-45">
                {item.label}
              </span>
            ),
          )}
        </nav>
        <span className="flex-1" />
        <Chip>Quota {quotaPct}%</Chip>
        {me && (
          <button
            type="button"
            onClick={() => logout.mutate()}
            title={`Signed in as ${me.display_name} — sign out`}
            aria-label={`Signed in as ${me.display_name}. Sign out`}
            className="flex h-[22px] w-[22px] flex-none items-center justify-center rounded-pill bg-border-soft text-2xs font-semibold text-ink-soft focus-visible:outline-2 focus-visible:outline-primary"
          >
            {initials(me.display_name)}
          </button>
        )}
      </header>
      <main className="flex flex-1 flex-col">{children}</main>
    </div>
  );
}
