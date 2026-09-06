import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL auto-cleanup needs vitest globals; we don't enable them, so register
// cleanup explicitly — without it rendered trees leak across tests.
afterEach(cleanup);

// jsdom has no ResizeObserver (V-057's coach-mark positioner uses one to
// re-measure its anchor) — a no-op stub is enough for tests that stub
// getBoundingClientRect() directly rather than relying on real layout.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

// BUG-167: jsdom has no `scrollIntoView` at all (a well-known jsdom gap,
// not tokenized/reflected in real layout either way) -- unmocked, a real
// call throws asynchronously inside the `requestAnimationFrame` callback
// that `SignalReport.tsx::focusReportJumpTarget` schedules, surfacing as
// a vitest "Unhandled Error" *after* the triggering test has already
// reported passed, not as that test's own failure. A no-op stub here is
// the standard fix (real scroll behavior is exactly what a real browser,
// not a real production risk, provides) -- a test that needs to assert
// scrollIntoView WAS called still overrides this with its own `vi.fn()`.
Element.prototype.scrollIntoView ??= () => {};
