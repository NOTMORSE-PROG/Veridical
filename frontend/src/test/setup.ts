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
