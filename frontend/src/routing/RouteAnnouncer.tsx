import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";

/** Persistent live region for route changes, including browser POP events.
 * A change that only swaps a panel on the same page, and says so itself,
 * passes `{ quietRouteChange: true }` as its navigation state. */
export const QUIET_ROUTE_CHANGE = { quietRouteChange: true } as const;

export function RouteAnnouncer() {
  const location = useLocation();
  const firstPaint = useRef(true);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (firstPaint.current) {
      firstPaint.current = false;
      return;
    }
    if ((location.state as { quietRouteChange?: boolean } | null)?.quietRouteChange) return;
    setAnnouncement("");
    const frame = requestAnimationFrame(() => {
      const pageName = document.title.replace(/\s+-\s+VERIDICAL$/u, "");
      setAnnouncement(`Page changed: ${pageName}.`);
    });
    return () => cancelAnimationFrame(frame);
  }, [location.key]);

  return (
    <p role="status" aria-live="polite" aria-atomic="true" className="signal-live-region">
      {announcement}
    </p>
  );
}
