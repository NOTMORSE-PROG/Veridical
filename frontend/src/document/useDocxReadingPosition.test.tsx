import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { recordSourceReadingPlacement, useDocxReadingPosition } from "./useDocxReadingPosition";

class Probe {
  static active = new Set<Probe>();
  readonly callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; Probe.active.add(this); }
  observe() {}
  disconnect() { Probe.active.delete(this); }
  static emit() { for (const instance of this.active) instance.callback([], instance as unknown as ResizeObserver); }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); Probe.active.clear(); });

function setup() {
  let width = 800, itemHeight = 1000;
  vi.stubGlobal("ResizeObserver", Probe);
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => width);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => width ? 200 : 0);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const root = document.querySelector<HTMLDivElement>('[data-testid="reader"]');
    const item = this.hasAttribute("data-source-paragraph");
    const top = item ? 100 - (root?.scrollTop ?? 0) : 100;
    const height = item ? itemHeight : 200;
    return { top, bottom: top + height, left: 0, right: width, width, height } as DOMRect;
  });
  const scroll = vi.fn(function (this: HTMLElement, options: ScrollToOptions) {
    this.scrollTop = options.top ?? this.scrollTop;
  });
  function Fixture({ visible, enabled = true }: { visible: boolean; enabled?: boolean }) {
    const root = useRef<HTMLDivElement>(null);
    useDocxReadingPosition(root, visible, enabled);
    return <div ref={node => { root.current = node; if (node) Object.defineProperty(node, "scrollTo", { configurable: true, value: scroll }); }} data-testid="reader"><p data-source-paragraph="0">Actual long manuscript paragraph</p></div>;
  }
  const view = render(<Fixture visible />);
  const root = screen.getByTestId("reader");
  return { root, scroll, resize(w: number, h: number) { width = w; itemHeight = h; act(() => Probe.emit()); },
    show(visible: boolean, enabled = true) { view.rerender(<Fixture visible={visible} enabled={enabled} />); }, unmount: view.unmount };
}

describe("DOCX reading point across desktop reflow", () => {
  it("preserves a manually chosen point inside its actual paragraph after width reflow", () => {
    const c = setup(); c.root.scrollTop = 400; fireEvent.scroll(c.root);
    c.resize(600, 1600);
    expect(c.root.scrollTop).toBe(640);
    expect(c.scroll).toHaveBeenCalledWith({ top: 640, left: 0, behavior: "instant" });
  });
  it("retains the point while the manuscript tab is hidden and restores on showing it", () => {
    const c = setup(); c.root.scrollTop = 400; fireEvent.scroll(c.root);
    c.resize(0, 1000); c.show(false);
    c.resize(600, 1600); c.show(true);
    expect(c.root.scrollTop).toBe(640);
    c.unmount(); expect(Probe.active.size).toBe(0);
  });
  it("adopts explicit source placement instead of resurrecting an earlier manual point", () => {
    const c = setup(); c.root.scrollTop = 200; fireEvent.scroll(c.root);
    c.root.scrollTop = 600; act(() => recordSourceReadingPlacement(c.root));
    c.resize(600, 1600);
    expect(c.root.scrollTop).toBe(960);
    c.show(true, false); expect(Probe.active.size).toBe(0);
  });
});
