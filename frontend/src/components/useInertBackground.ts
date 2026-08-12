// Shared by Modal.tsx and the V-057 coach-mark tour (CoachMark.tsx) --
// extracted from Modal.tsx (BUG-020) so a coach-mark step and a real
// Modal opened around the same time share ONE counter instead of each
// independently toggling #root's `inert` attribute and fighting over
// it (whichever closed first would incorrectly remove `inert` while
// the other was still open).
import { useEffect } from "react";

let openCount = 0;

export function useInertBackground() {
  useEffect(() => {
    const appRoot = document.getElementById("root");
    openCount += 1;
    if (appRoot && openCount === 1) appRoot.setAttribute("inert", "");
    return () => {
      openCount -= 1;
      if (appRoot && openCount === 0) appRoot.removeAttribute("inert");
    };
  }, []);
}
