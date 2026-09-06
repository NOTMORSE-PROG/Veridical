import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { markFlagViewed, useViewedFlagIds } from "./flagViewed";

describe("flagViewed", () => {
  afterEach(() => window.localStorage.clear());

  it("marks a flag viewed and reads it back for the same instructor", () => {
    const { result } = renderHook(() => useViewedFlagIds(1));
    expect(result.current.has(42)).toBe(false);

    act(() => markFlagViewed(1, 42));
    // markFlagViewed writes to localStorage and dispatches a same-tab
    // change event; the hook's own listener re-reads on that event.
    expect(result.current.has(42)).toBe(true);
  });

  it("BUG-183-class isolation: one instructor's viewed marks never appear for another, even on the same device", () => {
    act(() => markFlagViewed(1, 42));
    const { result: instructorB } = renderHook(() => useViewedFlagIds(2));
    expect(instructorB.current.has(42)).toBe(false);

    // And the reverse: instructor A's own hook still sees it.
    const { result: instructorA } = renderHook(() => useViewedFlagIds(1));
    expect(instructorA.current.has(42)).toBe(true);
  });

  it("does not conflate two different instructors' marks in the same storage area", () => {
    act(() => {
      markFlagViewed(1, 10);
      markFlagViewed(2, 20);
    });
    const { result: instructorA } = renderHook(() => useViewedFlagIds(1));
    const { result: instructorB } = renderHook(() => useViewedFlagIds(2));
    expect([...instructorA.current]).toEqual([10]);
    expect([...instructorB.current]).toEqual([20]);
  });

  it("is a no-op for an unauthenticated instructor id", () => {
    act(() => markFlagViewed(undefined, 99));
    const { result } = renderHook(() => useViewedFlagIds(undefined));
    expect(result.current.size).toBe(0);
    // Confirms nothing was ever written under a bogus/shared key either.
    expect(window.localStorage.length).toBe(0);
  });

  it("does not write a flag id twice", () => {
    act(() => {
      markFlagViewed(1, 42);
      markFlagViewed(1, 42);
    });
    const raw = window.localStorage.getItem("veridical.flags-viewed.v1.1");
    expect(JSON.parse(raw ?? "[]")).toEqual([42]);
  });

  it("degrades to an empty set, never throws, when storage access fails", () => {
    const original = window.localStorage.getItem;
    window.localStorage.getItem = () => {
      throw new Error("storage disabled (private browsing)");
    };
    try {
      const { result } = renderHook(() => useViewedFlagIds(1));
      expect(result.current.size).toBe(0);
    } finally {
      window.localStorage.getItem = original;
    }
  });

  it("propagates a change to another hook instance reading the same instructor's ids", () => {
    const { result: first } = renderHook(() => useViewedFlagIds(1));
    const { result: second } = renderHook(() => useViewedFlagIds(1));
    act(() => markFlagViewed(1, 7));
    expect(first.current.has(7)).toBe(true);
    expect(second.current.has(7)).toBe(true);
  });
});
