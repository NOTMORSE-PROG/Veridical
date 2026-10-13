import { useEffect, useRef, type RefObject } from "react";
import { useLocation } from "react-router";
import { useRouteFocus } from "../routing/useRouteFocus";

/** V-079: a return from a scrolled reader must leave the route heading clear
 * of fixed navigation. Filters and successful edits keep their current view. */
export function useLibraryRouteFocus(title: string, headingRef: RefObject<HTMLElement | null>) {
  useRouteFocus(title, headingRef);
  const { pathname } = useLocation();
  const scrolledRoute = useRef<string | null>(null);
  useEffect(() => {
    if (scrolledRoute.current === pathname || !headingRef.current) return;
    scrolledRoute.current = pathname;
    headingRef.current.scrollIntoView?.({ block: "start" });
  }, [headingRef, pathname, title]);
}
