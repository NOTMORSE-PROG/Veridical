import { type FocusEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { useQuota } from "../api/useQuota";
import { useLogout, useMe } from "../auth/useAuth";
import { SIGNAL_NAVIGATION } from "../config/signalRoutes";
import { CoachMark } from "../onboarding/CoachMark";
import { TourProvider, useTour } from "../onboarding/TourContext";
import { SignalMark, SignalWordmark } from "../ui/SignalMark";
import { isWorkspaceSample } from "../demo/workspaceFixture";

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

function CapacityStatement({ compact = false }: { compact?: boolean }) {
  const { data: quota, isPending, isError } = useQuota();
  if (isWorkspaceSample()) return <span className="signal-capacity">Document review</span>;
  if (isPending) {
    return <span className="signal-capacity" role="status">Checking capacity…</span>;
  }
  if (isError || !quota) return <span className="signal-capacity">Capacity unavailable</span>;
  const remaining = Math.max(0, quota.calls_remaining);
  return (
    <span className="signal-capacity">
      <strong>{remaining.toLocaleString()}</strong>{" "}
      {compact ? "calls left" : `AI call${remaining === 1 ? "" : "s"} available today`}
    </span>
  );
}

function WorkspaceNavigation({ label }: { label: string }) {
  const location = useLocation();
  return (
    <nav aria-label={label} className="signal-primary-navigation">
      <ul>
        {SIGNAL_NAVIGATION.map((item) => (
          <li key={item.label}>
            <Link
              to={item.to}
              aria-current={item.matches(location.pathname) ? "page" : undefined}
            >
              <span>{item.label}</span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ReplayTourControl({ onDone }: { onDone?: () => void }) {
  const { data: me } = useMe();
  const tour = useTour();
  if (!me) return null;
  return (
    <button
      type="button"
      data-tour="replay-tour-desktop"
      className="signal-account-action"
      onClick={() => {
        tour.replay();
        onDone?.();
      }}
    >
      Replay introduction
    </button>
  );
}

function AccountDisclosure() {
  const { data: me } = useMe();
  const logout = useLogout();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function handleBlur(event: FocusEvent<HTMLDivElement>) {
    const next = event.relatedTarget as Node | null;
    if (next && (event.currentTarget.contains(next) || next === triggerRef.current)) return;
    setOpen(false);
  }

  if (!me) return null;
  return (
    <div className="signal-account" onBlur={handleBlur}>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls="signal-account-panel"
        className="signal-account__trigger"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="signal-account__initials" aria-hidden="true">{initials(me.display_name)}</span>
        <span className="signal-account__label">Account</span>
      </button>
      {open && (
        <div id="signal-account-panel" className="signal-account__panel">
          <p title={me.display_name}>Signed in as <strong>{me.display_name}</strong></p>
          <ReplayTourControl onDone={() => setOpen(false)} />
          <button type="button" className="signal-account-action" onClick={() => logout.mutate()}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function SignalShell({ children }: { children?: ReactNode }) {
  return (
    <TourProvider>
      <div className="signal-theme signal-app" data-design="signal">
        <a href="#main-content" className="signal-skip-link">Skip to main content</a>

        <header className="signal-workspace-header">
          <div className="signal-workspace-header__inner">
            <Link to="/dashboard" className="signal-brand-link signal-on-dark" aria-label="VERIDICAL Review Desk">
              <SignalMark inverse />
              <SignalWordmark inverse />
            </Link>
            <WorkspaceNavigation label="Primary navigation" />
            <div className="signal-workspace-header__utilities">
              <CapacityStatement compact />
              <AccountDisclosure />
            </div>
          </div>
        </header>

        <main id="main-content" tabIndex={-1} className="signal-workspace-main">
          {children ?? <Outlet />}
        </main>

        <CoachMark />
      </div>
    </TourProvider>
  );
}
