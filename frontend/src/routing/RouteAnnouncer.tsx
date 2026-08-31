import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";

/** Persistent live region for route changes, including browser POP events. */
export function RouteAnnouncer() {
  const location = useLocation();
  const firstPaint = useRef(true);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (firstPaint.current) {
      firstPaint.current = false;
      return;
    }
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
