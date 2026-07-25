// Client-side mirror of the server-side route guard (V-014 AC:
// unauthenticated -> 4a). The server is the real guard (every protected
// endpoint checks the session itself); this only avoids flashing a
// protected page before that check comes back.
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useMe } from "./useAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();
  const location = useLocation();

  if (isPending) {
    return <div className="p-8 text-xs text-ink-faint">Loading…</div>;
  }
  if (!me) {
    return <Navigate to="/signin" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}
