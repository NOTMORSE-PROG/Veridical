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

    // A Link can carry its origin control in route state. Registering at the
    // destination is resilient to async source rendering and avoids relying
    // solely on the source click handler surviving a route transition.
    if (returnFocus && navigationType !== "POP" && !registeredReturnRef.current) {
      pendingReturnFocus = {
        returnPath: returnFocus.returnPath,
        destinationPath: location.pathname,
        elementId: returnFocus.elementId,
        armed: true,
      };
      registeredReturnRef.current = true;
    }

    const pending = pendingReturnFocus;
    if (pending && location.pathname === pending.destinationPath && navigationType !== "POP") {
      pending.armed = true;
    } else if (
      pending
      && pending.armed
      && navigationType === "POP"
      && location.pathname === pending.returnPath
    ) {
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
      const observer = new MutationObserver(() => {
        if (!restoreRegisteredFocus(pending.elementId)) return;
        pendingReturnFocus = null;
        observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
      return () => observer.disconnect();
    } else if (
      pending
      && navigationType !== "POP"
      && location.pathname !== pending.returnPath
      && location.pathname !== pending.destinationPath
    ) {
      pendingReturnFocus = null;
    }

    // StrictMode's development-only effect replay uses the same component
    // instance; this ref prevents the replay from looking like a second route.
    if (ranRef.current) return;
    ranRef.current = true;
    if (hasNavigatedOnce) headingRef.current?.focus();
    hasNavigatedOnce = true;
  }, [headingRef, location.pathname, navigationType, returnFocus, title]);
}
