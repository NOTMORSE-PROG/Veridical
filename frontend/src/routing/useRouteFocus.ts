// SPA route changes do not move focus by default. Each screen registers its
// main heading here; browser Back/Forward receives the same focus treatment
// as an in-app link. A focused return control can also be registered for a
// source-detail round trip, preserving the instructor's review position.
import { type RefObject, useEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router";

interface PendingReturnFocus {
  returnPath: string;
  destinationPath: string;
  elementId: string;
  armed: boolean;
}

export interface RouteReturnFocusRegistration {
  returnPath: string;
  elementId: string;
}

let hasNavigatedOnce = false;
let pendingReturnFocus: PendingReturnFocus | null = null;

// BUG-167: a return control that's genuinely never going to appear (its
// list view reset to a state that no longer renders it, the flag was
// resolved/removed, etc.) must not leave focus abandoned on <body>
// forever -- bounded so the MutationObserver below always resolves one
// way or another within a fixed window.
const RETURN_FOCUS_OBSERVER_TIMEOUT_MS = 500;

export function rememberRouteReturnFocus(
  returnPath: string,
  destinationPath: string,
  elementId: string,
) {
  pendingReturnFocus = { returnPath, destinationPath, elementId, armed: false };
}

function restoreRegisteredFocus(elementId: string): boolean {
  const target = document.getElementById(elementId);
  if (!target) return false;

  target.focus({ preventScroll: true });
  requestAnimationFrame(() => {
    const bounds = target.getBoundingClientRect();
    if (bounds.top < 0 || bounds.bottom > window.innerHeight) {
      target.scrollIntoView({ block: "nearest" });
    }
  });
  return true;
}

export function useRouteFocus(
  title: string,
  headingRef: RefObject<HTMLElement | null>,
  returnFocus?: RouteReturnFocusRegistration,
) {
  const location = useLocation();
  const navigationType = useNavigationType();
  const ranRef = useRef(false);
  const registeredReturnRef = useRef(false);

  useEffect(() => {
    document.title = title;
    // BUG-167: compared with the FULL path, including search, everywhere in
    // this hook -- a return control's own list-view/filter/pagination state
    // routinely lives in query params (SignalReviewSections.tsx's
    // `flags_view`/`flags_clusters_open`/`flags_visible`), and comparing
    // bare pathnames let two visually different return states (one where
    // the control exists, one where it doesn't) both read as "the same
    // return path." Harmless for a destination page with no meaningful
    // search (`location.search` is just "").
    const fullPath = location.pathname + location.search;

    // A Link can carry its origin control in route state. Registering at the
    // destination is resilient to async source rendering and avoids relying
    // solely on the source click handler surviving a route transition.
    if (returnFocus && navigationType !== "POP" && !registeredReturnRef.current) {
      pendingReturnFocus = {
        returnPath: returnFocus.returnPath,
        destinationPath: fullPath,
        elementId: returnFocus.elementId,
        armed: true,
      };
      registeredReturnRef.current = true;
    }

    const pending = pendingReturnFocus;
    if (pending && fullPath === pending.destinationPath && navigationType !== "POP") {
      pending.armed = true;
    } else if (pending && pending.armed && fullPath === pending.returnPath) {
      // BUG-167: deliberately not gated on `navigationType === "POP"`
      // anymore -- an in-app link back to the exact origin (a breadcrumb,
      // say) is a PUSH, not a POP, but it is just as much "we're back"
      // as the browser's own Back button, and an instructor has no way to
      // know the internal navigation-type distinction produces two
      // different experiences for what looks like the same action.
      if (restoreRegisteredFocus(pending.elementId)) {
        // React StrictMode immediately replays a newly mounted effect in
        // development. Mark this route pass as handled before clearing the
        // shared registration, or the replay will treat it as an ordinary
        // route change and move focus from the restored control to the H1.
        ranRef.current = true;
        pendingReturnFocus = null;
        return;
      }

      // Detail content can arrive after the route itself. Observe only until
      // the registered return control exists, then disconnect immediately.
      // BUG-167: a control that never appears at all (its own list view
      // reset to a state that no longer renders it) used to leave this
      // observer running forever with focus abandoned on <body> -- worse
      // than the plain "move focus to the heading" fallback every OTHER
      // route change gets. Bounded: whichever of "found it" / "timed out"
      // happens first wins, and the timeout path falls back to the same
      // heading-focus behavior an ordinary route change already uses.
      let settled = false;
      const observer = new MutationObserver(() => {
        if (settled || !restoreRegisteredFocus(pending.elementId)) return;
        settled = true;
        pendingReturnFocus = null;
        observer.disconnect();
        window.clearTimeout(timeoutId);
      });
      observer.observe(document.body, { childList: true, subtree: true });
      const timeoutId = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        pendingReturnFocus = null;
        headingRef.current?.focus();
      }, RETURN_FOCUS_OBSERVER_TIMEOUT_MS);
      return () => {
        observer.disconnect();
        window.clearTimeout(timeoutId);
      };
    } else if (
      pending
      && navigationType !== "POP"
      && fullPath !== pending.returnPath
      && fullPath !== pending.destinationPath
    ) {
      pendingReturnFocus = null;
    }

    // StrictMode's development-only effect replay uses the same component
    // instance; this ref prevents the replay from looking like a second route.
    if (ranRef.current) return;
    ranRef.current = true;
    if (hasNavigatedOnce) headingRef.current?.focus();
    hasNavigatedOnce = true;
  }, [headingRef, location.pathname, location.search, navigationType, returnFocus, title]);
}
