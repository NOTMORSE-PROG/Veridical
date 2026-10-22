import { useEffect, useRef, type RefObject } from "react";
import { useLocation } from "react-router";
import { clearRouteReturnFocus, rememberRouteReturnFocus, useRouteFocus, type RouteReturnFocusRegistration } from "../routing/useRouteFocus";
import { LIBRARY_RETURN } from "../config/signalRoutes";

/** V-079: a return from a scrolled reader must leave the route heading clear
 * of fixed navigation. Filters and successful edits keep their current view. */
export function useLibraryRouteFocus(
  title: string, headingRef: RefObject<HTMLElement | null>, returnFocus?: RouteReturnFocusRegistration | null,
) {
  const location = useLocation();
  const registeredEntry = useRef<string | null>(null);
  const returnPath = returnFocus?.returnPath;
  const elementId = returnFocus?.elementId;
  const sourceArrival = returnFocus !== undefined;
  // Rearm on source POP as well as PUSH, so Forward -> source -> Back works
  // after the previous return consumed the shared registration.
  useEffect(() => {
    if (!sourceArrival || registeredEntry.current === location.key) return;
    registeredEntry.current = location.key;
    if (!returnPath || !elementId) {
      clearRouteReturnFocus(LIBRARY_RETURN.actionPrefix);
      return;
    }
    rememberRouteReturnFocus(
      returnPath, location.pathname + location.search, elementId, { armed: true },
    );
  }, [location.key, location.pathname, location.search, returnPath, elementId, sourceArrival]);
  useRouteFocus(title, headingRef);
  const { pathname } = location;
  const scrolledRoute = useRef<string | null>(null);
  useEffect(() => {
    if (scrolledRoute.current === pathname || !headingRef.current
      || document.activeElement !== headingRef.current) return;
    scrolledRoute.current = pathname;
    headingRef.current.scrollIntoView?.({ block: "start" });
  }, [headingRef, pathname, title]);
}
