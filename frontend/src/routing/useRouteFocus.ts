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
  fallbackElementId?: string | readonly string[];
  scrollBlock?: ScrollLogicalPosition;
}

export interface RouteReturnFocusRegistration {
  returnPath: string;
  elementId: string;
  fallbackElementId?: string | readonly string[];
  scrollBlock?: ScrollLogicalPosition;
  retainOnSearchChange?: boolean;
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
  options: { armed?: boolean; fallbackElementId?: string | readonly string[]; scrollBlock?: ScrollLogicalPosition } = {},
) {
  pendingReturnFocus = { returnPath, destinationPath, elementId, armed: options.armed ?? false, fallbackElementId: options.fallbackElementId, scrollBlock: options.scrollBlock };
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

function fallbackTarget(ids: string | readonly string[] | undefined): HTMLElement | null {
  for (const id of typeof ids === "string" ? [ids] : ids ?? []) {
    const target = document.getElementById(id);
    if (target) return target;
  }
  return null;
}

function restoreRegisteredFocus(elementId: string, scrollBlock: ScrollLogicalPosition = "nearest"): HTMLElement | null {
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
  let ownsPlacement = true;
  const removePlacementIntent = observeUserIntent(target, () => {
    ownsPlacement = false;
    removePlacementIntent();
  });
  requestAnimationFrame(() => {
    removePlacementIntent();
    if (!ownsPlacement || !target.isConnected || document.activeElement !== target) return;
    const bounds = target.getBoundingClientRect();
    // A fixed bar at the bottom (bulk selection, uploads tray) reserves its
    // height as scroll padding; a control under it is not in view.
    const reserved = Number.parseFloat(getComputedStyle(document.documentElement).scrollPaddingBottom) || 0;
    if (scrollBlock !== "nearest" || bounds.top < 0 || bounds.bottom > window.innerHeight - reserved) {
      target.scrollIntoView({ block: scrollBlock });
    }
  });
  return target;
}

function restoreFallbackFocus(ids: string | readonly string[] | undefined, heading: HTMLElement | null) {
  for (const id of typeof ids === "string" ? [ids] : ids ?? []) {
    if (restoreRegisteredFocus(id)) return;
  }
  if (heading && !restoreRegisteredFocus(heading.id)) heading.focus();
}

function observeUserIntent(
  target: HTMLElement | null,
  retire: () => void,
  restoring: () => boolean = () => false,
  expectedFocus: (target: EventTarget | null) => boolean = () => false,
): () => void {
  const onFocus = (event: FocusEvent) => {
    if (!restoring() && event.target !== target && !expectedFocus(event.target)) retire();
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
  fallbackElementId?: string | readonly string[];
  scrollBlock?: ScrollLogicalPosition;
}

function observeRestoredFocus(
  target: HTMLElement,
  headingRef: RefObject<HTMLElement | null>,
  retire: () => void,
  fallbackElementId?: string | readonly string[],
  nativeHashTarget?: HTMLElement | null,
  scrollBlock?: ScrollLogicalPosition,
): () => void {
  const scope = headingRef.current?.closest("main") ?? target.closest("main") ?? document.body;
  let disposed = false;
  let settlingHash = Boolean(nativeHashTarget && nativeHashTarget !== target);
  let hashFrame: number | undefined;
  let removeIntent = () => {};
  const observer = new MutationObserver(() => {
    if (disposed || target.isConnected) return;
    const active = document.activeElement;
    const heading = fallbackTarget(fallbackElementId) ?? headingRef.current;
    const recover = scope.isConnected && heading?.isConnected
      && (active === document.body || active === null || active === target);
    stop();
    if (!recover) return;
    const replacement = document.getElementById(target.id);
    if (replacement && scope.contains(replacement) && restoreRegisteredFocus(target.id, scrollBlock)) return;
    restoreFallbackFocus(fallbackElementId, headingRef.current);
  });
  const dispose = () => {
    disposed = true;
    observer.disconnect();
    if (hashFrame !== undefined) cancelAnimationFrame(hashFrame);
    removeIntent();
  };
  const stop = () => {
    dispose();
    retire();
  };
  removeIntent = observeUserIntent(target, stop, () => false,
    focused => settlingHash && focused === nativeHashTarget);
  if (settlingHash) hashFrame = requestAnimationFrame(() => {
    settlingHash = false;
    // A POP can focus its hash after our layout effect restored the exact
    // source action. Allow only that expected settlement in this frame;
    // keyboard, pointer, wheel, blur or any other focus already retires it.
    if (!disposed && document.activeElement === nativeHashTarget && target.isConnected) {
      restoreRegisteredFocus(target.id, scrollBlock);
    }
  });
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
    const watchRestoration = (target: HTMLElement, fallbackElementId?: string | readonly string[], scrollBlock?: ScrollLogicalPosition) => {
      const ownership = { path: fullPath, target, fallbackElementId, scrollBlock };
      restoredRef.current = ownership;
      let hashTarget: HTMLElement | null = null;
      if (navigationType === "POP" && location.hash) {
        try { hashTarget = document.getElementById(decodeURIComponent(location.hash.slice(1))); }
        catch { /* A malformed hash supplies no expected focus settlement. */ }
      }
      return observeRestoredFocus(target, headingRef, () => {
        if (restoredRef.current === ownership) restoredRef.current = null;
      }, fallbackElementId, hashTarget, scrollBlock);
    };
    const restored = restoredRef.current;
    if (restored?.path === fullPath && restored.target.isConnected
      && document.activeElement === restored.target) {
      return watchRestoration(restored.target, restored.fallbackElementId, restored.scrollBlock);
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
        scrollBlock: returnFocus.scrollBlock,
      };
      registeredReturnRef.current = true;
    }

    const pending = pendingReturnFocus;
    // A manuscript's reading mode/page can change without leaving its source
    // task. Retain only this already-owned origin; never revive a cleared or
    // replaced registration, or extend it to another route.
    if (returnFocus?.retainOnSearchChange && pending
      && pending.returnPath === returnFocus.returnPath && pending.elementId === returnFocus.elementId
      && pending.destinationPath.split("?")[0] === location.pathname) pending.destinationPath = fullPath;
    if (pending && fullPath === pending.destinationPath && navigationType !== "POP") {
      pending.armed = true;
    } else if (pending && pending.armed && fullPath === pending.returnPath) {
      // BUG-167: deliberately not gated on `navigationType === "POP"`
      // anymore -- an in-app link back to the exact origin (a breadcrumb,
      // say) is a PUSH, not a POP, but it is just as much "we're back"
      // as the browser's own Back button, and an instructor has no way to
      // know the internal navigation-type distinction produces two
      // different experiences for what looks like the same action.
      const target = restoreRegisteredFocus(pending.elementId, pending.scrollBlock);
      if (target) {
        // React StrictMode immediately replays a newly mounted effect in
        // development. Mark this route pass as handled before clearing the
        // shared registration, or the replay will treat it as an ordinary
        // route change and move focus from the restored control to the H1.
        ranRef.current = true;
        pendingReturnFocus = null;
        return watchRestoration(target, pending.fallbackElementId, pending.scrollBlock);
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
        const appeared = restoreRegisteredFocus(pending.elementId, pending.scrollBlock);
        restoring = false;
        if (!appeared) return;
        finishSearch();
        disposeOwnership = watchRestoration(appeared, pending.fallbackElementId, pending.scrollBlock);
      });
      const scope = headingRef.current?.closest("main") ?? document.body;
      observer.observe(scope, { childList: true, subtree: true });
      const timeoutId = defer ? undefined : window.setTimeout(() => {
        if (settled) return;
        finishSearch();
        restoreFallbackFocus(pending.fallbackElementId, headingRef.current);
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
    if (hasNavigatedOnce) {
      if (!location.hash) {
        if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
        if (
          typeof window.scrollTo === "function"
          && (!navigator.userAgent.includes("jsdom") || "mock" in window.scrollTo)
        ) {
          window.scrollTo(0, 0);
        }
      }
      headingRef.current?.focus({ preventScroll: true });
    }
    hasNavigatedOnce = true;
  }, [headingRef, location.pathname, location.search, location.hash, navigationType, returnFocus, title, deferredPrefix, deferredPending]);
}
