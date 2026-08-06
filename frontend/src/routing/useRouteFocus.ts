// SPA route changes don't move focus by default (React Router does nothing
// about it). Every screen's main heading calls this once on mount so a
// keyboard/screen-reader user isn't stranded on a control that no longer
// exists after navigating here. Skipped on the very first paint of a hard
// page load (nothing to move focus away from yet).
import { type RefObject, useEffect, useRef } from "react";

let hasNavigatedOnce = false;

export function useRouteFocus(title: string, headingRef: RefObject<HTMLElement | null>) {
  // StrictMode's dev-only double-invoke (mount -> cleanup -> mount again)
  // replays this effect on the SAME component instance without a real
  // second navigation happening. A bare module-level flag can't tell that
  // replay apart from a genuine next screen, so a ref local to this
  // instance (which survives the replay untouched) guards it: only the
  // first invocation for this mount is allowed to read/advance the shared
  // flag.
  const ranRef = useRef(false);

  useEffect(() => {
    document.title = title;
    if (ranRef.current) return;
    ranRef.current = true;
    if (hasNavigatedOnce) headingRef.current?.focus();
    hasNavigatedOnce = true;
    // Only re-run when the screen (and its title) actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title]);
}
