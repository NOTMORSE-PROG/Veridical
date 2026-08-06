// Client-side mirror of the server-side route guard (V-014 AC:
// unauthenticated -> 4a). The server is the real guard (every protected
// endpoint checks the session itself); this only avoids flashing a
// protected page before that check comes back.
//
// BUG-010 fix: `useMe` only resolves to `null` on a CONFIRMED 401 (see
// useAuth.ts) -- any other failure (a 5xx, a network error, Render's
// cold-start blip, D-004) surfaces as `isError`, not as `me === null`.
// Treating both identically bounced a still-authenticated instructor to
// /signin on a transient backend hiccup, indistinguishable from a real
// session expiry -- the same honest-signal problem ground rule 3/9 names
// for AI-flag wording, applied to auth state.
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useMe } from "./useAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending, isError, refetch } = useMe();
  const location = useLocation();

  if (isPending) {
    return (
      <div role="status" aria-live="polite" aria-busy="true" className="p-8 text-sm text-ink-tertiary">
        Checking your session.
      </div>
    );
  }
  if (isError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-2 p-8 text-sm text-status-attention-text">
        <p>
          VERIDICAL could not reach the server to check your session. This is usually temporary.
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
        >
          Try again
        </button>
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/signin" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}
