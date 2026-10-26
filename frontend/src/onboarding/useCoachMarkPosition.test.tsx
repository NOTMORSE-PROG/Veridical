import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCoachMarkPosition } from "./useCoachMarkPosition";

const rect = (top: number, height: number) => ({ top, bottom: top + height, left: 0, right: 200, width: 200, height, x: 0, y: top, toJSON() {} });

afterEach(() => {
  document.body.replaceChildren();
  document.documentElement.style.removeProperty("--signal-workspace-header-height");
  document.documentElement.style.removeProperty("font-size");
  vi.unstubAllGlobals();
});

describe("desktop coach-mark header clearance", () => {
  it("uses the actual header edge and recomputes when that header resizes", () => {
    let notify: () => void = () => {};
    const observe = vi.fn();
    vi.stubGlobal("ResizeObserver", class { constructor(callback: () => void) { notify = callback; } observe = observe; disconnect = vi.fn(); });
    const header = document.createElement("header");
    header.className = "signal-workspace-header";
    let height = 80;
    header.getBoundingClientRect = () => rect(0, height);
    const anchor = document.createElement("button");
    anchor.getBoundingClientRect = () => rect(85, 40);
    const scroll = vi.fn();
    anchor.scrollIntoView = scroll;
    document.body.append(header, anchor);
    const { unmount } = renderHook(() => useCoachMarkPosition(anchor));
    expect(scroll).toHaveBeenCalledOnce();
    expect(observe).toHaveBeenCalledWith(header);
    height = 50;
    act(() => notify());
    expect(scroll).toHaveBeenCalledOnce();
    unmount();
  });

  it("does not try to scroll an anchor inside the desktop header", () => {
    const header = document.createElement("header");
    header.className = "signal-workspace-header";
    header.getBoundingClientRect = () => rect(0, 80);
    const anchor = document.createElement("button");
    anchor.getBoundingClientRect = () => rect(16, 48);
    const scroll = vi.fn();
    anchor.scrollIntoView = scroll;
    header.append(anchor);
    document.body.append(header);
    const { unmount } = renderHook(() => useCoachMarkPosition(anchor));
    expect(scroll).not.toHaveBeenCalled();
    unmount();
  });

  it("resolves the one desktop token against the actual root font size", () => {
    document.documentElement.style.setProperty("--signal-workspace-header-height", "5rem");
    document.documentElement.style.fontSize = "20px";
    const anchor = document.createElement("button");
    anchor.getBoundingClientRect = () => rect(105, 40);
    const scroll = vi.fn();
    anchor.scrollIntoView = scroll;
    document.body.append(anchor);
    const { unmount } = renderHook(() => useCoachMarkPosition(anchor));
    expect(scroll).toHaveBeenCalledOnce();
    unmount();
  });
});
