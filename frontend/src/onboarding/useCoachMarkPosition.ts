// Anchored positioning for the coach-mark tour (V-057). A
// getBoundingClientRect-based positioner, not CSS anchor positioning:
// checked live 2026-08 (caniuse.com/css-anchor-positioning), global
// support sits around the mid-80s% with Safari lagging on the flip
// behavior this component needs anyway — and the flip/clamp/header-
// collision logic below is needed regardless of which primitive
// supplies the raw rect, so CSS anchor positioning would only relocate
// part of the problem, not remove it. A capstone defense running on an
// unknown machine is the wrong place to depend on the newest ~15% gap.
import { useEffect, useState } from "react";

export interface CoachMarkRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

export type Placement = "bottom" | "top" | "right" | "left";

export interface CoachMarkPosition {
  anchorRect: CoachMarkRect;
  placement: Placement;
  /** Tooltip card's own top-left, already clamped to the viewport. */
  cardTop: number;
  cardLeft: number;
}

const GAP = 12; // --spacing-3
const VIEWPORT_MARGIN = 16; // --spacing-4
const CARD_WIDTH = 320;
const CARD_HEIGHT_ESTIMATE = 180; // enough to pick a placement before layout; real height clamps at render

function fitsBelow(rect: DOMRect): boolean {
  return rect.bottom + GAP + CARD_HEIGHT_ESTIMATE <= window.innerHeight - VIEWPORT_MARGIN;
}
function fitsAbove(rect: DOMRect): boolean {
  return rect.top - GAP - CARD_HEIGHT_ESTIMATE >= VIEWPORT_MARGIN;
}
function fitsRight(rect: DOMRect): boolean {
  return rect.right + GAP + CARD_WIDTH <= window.innerWidth - VIEWPORT_MARGIN;
}

function computePosition(el: HTMLElement): CoachMarkPosition {
  const rect = el.getBoundingClientRect();
  const placement: Placement = fitsBelow(rect)
    ? "bottom"
    : fitsAbove(rect)
      ? "top"
      : fitsRight(rect)
        ? "right"
        : "left";

  let cardTop: number;
  let cardLeft: number;
  if (placement === "bottom") {
    cardTop = rect.bottom + GAP;
    cardLeft = rect.left;
  } else if (placement === "top") {
    cardTop = rect.top - GAP - CARD_HEIGHT_ESTIMATE;
    cardLeft = rect.left;
  } else if (placement === "right") {
    cardTop = rect.top;
    cardLeft = rect.right + GAP;
  } else {
    cardTop = rect.top;
    cardLeft = rect.left - GAP - CARD_WIDTH;
  }

  // Clamp into the viewport on every axis.
  cardLeft = Math.min(
    Math.max(cardLeft, VIEWPORT_MARGIN),
    window.innerWidth - CARD_WIDTH - VIEWPORT_MARGIN,
  );
  cardTop = Math.min(Math.max(cardTop, VIEWPORT_MARGIN), window.innerHeight - VIEWPORT_MARGIN);

  return {
    anchorRect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
    placement,
    cardTop,
    cardLeft,
  };
}

// Reads the app's own header-height tokens rather than a hardcoded
// number (charter rule 7) -- computed once per call since these are
// CSS custom properties, not JS constants.
function headerHeightPx(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(
    window.innerWidth >= 768 ? "--header-height" : "--header-height-mobile",
  );
  const rem = parseFloat(raw);
  return Number.isFinite(rem) ? rem * 16 : 64;
}

export function useCoachMarkPosition(el: HTMLElement | null): CoachMarkPosition | null {
  const [position, setPosition] = useState<CoachMarkPosition | null>(null);

  useEffect(() => {
    if (!el) {
      setPosition(null);
      return;
    }

    let cancelled = false;

    function recompute() {
      if (!el || cancelled) return;
      const rect = el.getBoundingClientRect();
      // Header-collision guard (WCAG 2.4.11): if the anchor sits under
      // the sticky header, scroll it into view before measuring for
      // real, so the coach mark never has to describe an element the
      // header is currently covering.
      if (rect.top < headerHeightPx() + GAP) {
        el.scrollIntoView({ block: "center", behavior: "auto" });
      }
      setPosition(computePosition(el));
    }

    recompute();

    const resizeObserver = new ResizeObserver(recompute);
    resizeObserver.observe(el);
    window.addEventListener("resize", recompute);
    document.addEventListener("scroll", recompute, { passive: true, capture: true });

    return () => {
      cancelled = true;
      resizeObserver.disconnect();
      window.removeEventListener("resize", recompute);
      document.removeEventListener("scroll", recompute, true);
    };
  }, [el]);

  return position;
}
