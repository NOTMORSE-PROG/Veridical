// SPA route changes do not move focus by default. Each screen registers its
// main heading here; browser Back/Forward receives the same focus treatment
// as an in-app link. A focused return control can also be registered for a
// source-detail round trip, preserving the instructor's review position.
import { type RefObject, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router";

interface PendingReturnFocus {
  returnPath: string;
  destinationPath: string;
  elementId: string;
  armed: boolean;
  fallbackElementId?: string;
}

export interface RouteReturnFocusRegistration {
  returnPath: string;
  elementId: string;
  fallbackElementId?: string;
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
  options: { armed?: boolean; fallbackElementId?: string } = {},
) {
  pendingReturnFocus = { returnPath, destinationPath, elementId, armed: options.armed ?? false, fallbackElementId: options.fallbackElementId };
}

/** Retire only registrations owned by the named workflow's action prefix. */
export function clearRouteReturnFocus(actionPrefix: string) {
  if (pendingReturnFocus?.elementId.startsWith(actionPrefix)) pendingReturnFocus = null;
}

/** Let a pending source return own asynchronous restoration before URL entry focus. */
export function hasPendingRouteReturnFocus(path: string) {
  return pendingReturnFocus?.armed === true && pendingReturnFocus.returnPath === path;
}

/** Only the exact origin may reveal a registered comparison's page. */
export function pendingRouteReturnElement(path: string) {
  return pendingReturnFocus?.armed && pendingReturnFocus.returnPath === path
    ? pendingReturnFocus.elementId : undefined;
}

function restoreRegisteredFocus(elementId: string): HTMLElement | null {
  const target = document.getElementById(elementId);
  if (!target) return null;

  // The source control can live inside nested native disclosures which
  // remount closed after navigation. Reveal it before restoring focus.
  let disclosure = target.parentElement?.closest("details");
  while (disclosure) {
    disclosure.open = true;
    disclosure = disclosure.parentElement?.closest("details");
  }
  target.focus({ preventScroll: true });
  if (document.activeElement !== target) return null;
  requestAnimationFrame(() => {
    if (!target.isConnected || document.activeElement !== target) return;
    const bounds = target.getBoundingClientRect();
    if (bounds.top < 0 || bounds.bottom > window.innerHeight) {
      target.scrollIntoView({ block: "nearest" });
    }
  });
  return target;
}

function observeUserIntent(
  target: HTMLElement | null,
  retire: () => void,
  restoring: () => boolean = () => false,
): () => void {
  const onFocus = (event: FocusEvent) => {
    if (!restoring() && event.target !== target) retire();
  };
  // React can blur a still-connected node just before removing it. Neutral
  // focusout alone does not establish that the instructor moved focus.
  const onKey = (event: KeyboardEvent) => {
    if (["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", " "].includes(event.key)) retire();
  };
  const onPointer = (event: PointerEvent) => {
    if (!target || !target.contains(event.target as Node)) retire();
  };
  document.addEventListener("focusin", onFocus, true);
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("pointerdown", onPointer, true);
  window.addEventListener("blur", retire);
  window.addEventListener("wheel", retire, { passive: true });
  return () => {
    document.removeEventListener("focusin", onFocus, true);
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("pointerdown", onPointer, true);
    window.removeEventListener("blur", retire);
    window.removeEventListener("wheel", retire);
  };
}

interface RestoredFocus {
  path: string;
  target: HTMLElement;
  fallbackElementId?: string;
}

function observeRestoredFocus(
  target: HTMLElement,
  headingRef: RefObject<HTMLElement | null>,
  retire: () => void,
  fallbackElementId?: string,
): () => void {
  const scope = headingRef.current?.closest("main") ?? target.closest("main") ?? document.body;
  let disposed = false;
  let removeIntent = () => {};
  const observer = new MutationObserver(() => {
    if (disposed || target.isConnected) return;
    const active = document.activeElement;
    const heading = (fallbackElementId && document.getElementById(fallbackElementId)) || headingRef.current;
    const recover = scope.isConnected && heading?.isConnected
      && (active === document.body || active === null || active === target);
    stop();
    if (!recover) return;
    const replacement = document.getElementById(target.id);
    if (replacement && scope.contains(replacement) && restoreRegisteredFocus(target.id)) return;
    heading.focus();
  });
  const dispose = () => {
    disposed = true;
    observer.disconnect();
    removeIntent();
  };
  const stop = () => {
    dispose();
    retire();
  };
  removeIntent = observeUserIntent(target, stop);
  observer.observe(scope, { childList: true, subtree: true });
  // Effect cleanup leaves the record available for StrictMode's replay.
  // User intent or reconciliation retires it permanently instead.
  return dispose;
}

export function useRouteFocus(
  title: string,
  headingRef: RefObject<HTMLElement | null>,
  returnFocus?: RouteReturnFocusRegistration,
  asyncReturn?: { prefix: string; pending: boolean },
) {
  const location = useLocation();
  const navigationType = useNavigationType();
  const ranRef = useRef(false);
  const registeredReturnRef = useRef(false);
  const restoredRef = useRef<RestoredFocus | null>(null);
  const deferredPrefix = asyncReturn?.prefix;
  const deferredPending = asyncReturn?.pending;

  // Establish route/return focus before source panes place their requested
  // target in a passive effect, including when that source is already cached.
  useLayoutEffect(() => {
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
    const watchRestoration = (target: HTMLElement, fallbackElementId?: string) => {
      const ownership = { path: fullPath, target, fallbackElementId };
      restoredRef.current = ownership;
      return observeRestoredFocus(target, headingRef, () => {
        if (restoredRef.current === ownership) restoredRef.current = null;
      }, fallbackElementId);
    };
    const restored = restoredRef.current;
    if (restored?.path === fullPath && restored.target.isConnected
      && document.activeElement === restored.target) {
      return watchRestoration(restored.target, restored.fallbackElementId);
    }
    restoredRef.current = null;

    // A Link can carry its origin control in route state. Registering at the
    // destination is resilient to async source rendering and avoids relying
    // solely on the source click handler surviving a route transition.
    if (returnFocus && navigationType !== "POP" && !registeredReturnRef.current) {
      pendingReturnFocus = {
        returnPath: returnFocus.returnPath,
        destinationPath: fullPath,
        elementId: returnFocus.elementId,
        armed: true,
        fallbackElementId: returnFocus.fallbackElementId,
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
      const target = restoreRegisteredFocus(pending.elementId);
      if (target) {
        // React StrictMode immediately replays a newly mounted effect in
        // development. Mark this route pass as handled before clearing the
        // shared registration, or the replay will treat it as an ordinary
        // route change and move focus from the restored control to the H1.
        ranRef.current = true;
        pendingReturnFocus = null;
        return watchRestoration(target, pending.fallbackElementId);
      }

      // Async content may arrive after the route. Search is time-bounded;
      // successful restoration then owns focus only until user intent,
      // removal reconciliation or route cleanup (including cache refresh).
      const defer = deferredPending && deferredPrefix && pending.elementId.startsWith(deferredPrefix);
      // A pending collection owns its lifetime; a timer cannot decide that
      // a record is absent. Place focus before observing newer user intent.
      if (defer && (document.activeElement === document.body || !document.activeElement)) headingRef.current?.focus();
      let settled = false;
      let restoring = false;
      let disposeOwnership: (() => void) | undefined;
      const releaseSearch = () => {
        observer.disconnect();
        window.clearTimeout(timeoutId);
        removeIntent();
      };
      const finishSearch = () => {
        if (settled) return;
        settled = true;
        ranRef.current = true;
        if (pendingReturnFocus === pending) pendingReturnFocus = null;
        releaseSearch();
      };
      const observer = new MutationObserver(() => {
        if (settled) return;
        restoring = true;
        const appeared = restoreRegisteredFocus(pending.elementId);
        restoring = false;
        if (!appeared) return;
        finishSearch();
        disposeOwnership = watchRestoration(appeared, pending.fallbackElementId);
      });
      const scope = headingRef.current?.closest("main") ?? document.body;
      observer.observe(scope, { childList: true, subtree: true });
      const timeoutId = defer ? undefined : window.setTimeout(() => {
        if (settled) return;
        finishSearch();
        ((pending.fallbackElementId && document.getElementById(pending.fallbackElementId)) || headingRef.current)?.focus();
      }, RETURN_FOCUS_OBSERVER_TIMEOUT_MS);
      const removeIntent = observeUserIntent(null, finishSearch, () => restoring);
      return () => {
        releaseSearch();
        disposeOwnership?.();
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
  }, [headingRef, location.pathname, location.search, navigationType, returnFocus, title, deferredPrefix, deferredPending]);
}
