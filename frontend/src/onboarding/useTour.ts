// V-057 — tour state: which step is active, and the two persisted
// actions (dismiss, replay). The step POSITION itself is client-side
// only (resets to step 1 on a fresh page load or a Replay) -- only
// "has this instructor ever finished/dismissed the tour" is persisted
// server-side, reusing Instructor.onboarding_dismissed_at (the same
// field V-055's banner used) via the existing dismiss mutation plus a
// new replay one. This is a deliberate simplification of the original
// spec's per-step server persistence: the ticket's own AC3 asks for
// dismissal to be "remembered per user, not per session" -- it does not
// ask for mid-tour step position to survive a full reload, and a tour
// that always restarts at step 1 on Replay matches both of the owner's
// own reference screenshots.
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";
import { useDismissOnboarding, useMe, useReplayOnboarding } from "../auth/useAuth";
import { resolvePreviousTourStep, resolveTourStep, TOUR_STEPS } from "./tourSteps";

const POLL_MS = 400;
const MAX_POLLS = 12; // ~4.8s -- covers async-loaded anchors (e.g. EscalatedPanel's own query) without polling forever

export function useTour() {
  const { data: me } = useMe();
  const location = useLocation();
  const dismiss = useDismissOnboarding();
  const replay = useReplayOnboarding();

  const [stepIndex, setStepIndex] = useState(0);
  const [dismissedLocally, setDismissedLocally] = useState(false);
  // Bumped to force a re-resolve poll (DOM mutation with no route/step
  // change of its own, e.g. data finishing a fetch).
  const [pollTick, setPollTick] = useState(0);
  const pollCountRef = useRef(0);

  const active = Boolean(me) && !me?.onboarding_dismissed_at && !dismissedLocally;

  const resolved = active ? resolveTourStep(stepIndex, location.pathname) : null;

  // Poll a bounded number of times when the tour is active but nothing
  // resolves yet on this page -- catches an anchor that mounts after an
  // async fetch resolves, without a MutationObserver watching the whole
  // document.
  useEffect(() => {
    pollCountRef.current = 0;
  }, [stepIndex, location.pathname]);

  useEffect(() => {
    if (!active || resolved) return;
    if (pollCountRef.current >= MAX_POLLS) return;
    const timer = setTimeout(() => {
      pollCountRef.current += 1;
      setPollTick((t) => t + 1);
    }, POLL_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, resolved, pollTick]);

  function next() {
    if (!resolved) return;
    setStepIndex(resolved.index + 1);
  }

  function back() {
    if (!resolved) return;
    const previous = resolvePreviousTourStep(resolved.index, location.pathname);
    if (previous) setStepIndex(previous.index);
  }

  function dismissTour() {
    setDismissedLocally(true); // hide instantly, never gate on the mutation (Dashboard.tsx's own established pattern)
    dismiss.mutate();
  }

  function replayTour() {
    setDismissedLocally(false);
    setStepIndex(0);
    replay.mutate();
  }

  return {
    resolved,
    stepNumber: resolved ? resolved.index + 1 : 0,
    totalSteps: TOUR_STEPS.length,
    isFirst: resolved?.index === 0,
    isLast: resolved?.index === TOUR_STEPS.length - 1,
    next,
    back,
    dismiss: dismissTour,
    replay: replayTour,
  };
}
