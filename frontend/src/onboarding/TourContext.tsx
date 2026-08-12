// V-057 fix (ux-critic finding, live-reproduced): AppShell's Replay
// button and CoachMark each called useTour() independently, so they had
// two separate `dismissedLocally` booleans. Dismissing once, then
// clicking Replay, cleared the SERVER field (both instances share the
// same react-query cache entry) but not the OTHER instance's local
// flag -- a silent no-op on the second-or-later Replay click in one
// session, exactly the realistic case. One shared instance, provided
// once at the AppShell level, is the actual fix (not a smaller patch):
// there must be exactly one source of truth for "is the tour showing."
import { createContext, type ReactNode, useContext } from "react";
import { useTour as useTourInternal } from "./useTour";

type TourContextValue = ReturnType<typeof useTourInternal>;

const TourContext = createContext<TourContextValue | null>(null);

export function TourProvider({ children }: { children: ReactNode }) {
  const tour = useTourInternal();
  return <TourContext.Provider value={tour}>{children}</TourContext.Provider>;
}

export function useTour(): TourContextValue {
  const ctx = useContext(TourContext);
  if (!ctx) throw new Error("useTour() must be called within a <TourProvider>");
  return ctx;
}
