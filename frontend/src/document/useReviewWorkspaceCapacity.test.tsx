import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useReviewWorkspaceCapacity } from "./useReviewWorkspaceCapacity";

class ResizeProbe {
  static active = new Set<ResizeProbe>();
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; ResizeProbe.active.add(this); }
  observe() {}
  disconnect() { ResizeProbe.active.delete(this); }
  static emit() { for (const observer of [...this.active]) observer.callback([], observer as unknown as ResizeObserver); }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); ResizeProbe.active.clear(); });

describe("review workspace measures mounted content rather than a viewport guess", () => {
  function setup(initialWidth: number, initialThreshold = 856) {
    let width = initialWidth;
    let threshold = initialThreshold;
    vi.stubGlobal("ResizeObserver", ResizeProbe);
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) { return this.dataset.testid === "frame" ? width : 0; });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) { return { width: this.classList.contains("signal-review-workspace-threshold") ? threshold : width } as DOMRect; });
    const collapse = vi.fn();
    function Fixture() {
      const [loaded, show] = useState(false);
      const workspace = useReviewWorkspaceCapacity(collapse);
      return <><button onClick={() => show(current => !current)}>Toggle loaded frame</button>{loaded && <section ref={workspace.frame} data-testid="frame" data-split={workspace.split}>
        <span ref={workspace.threshold} className="signal-review-workspace-threshold" /><input aria-label="Retained draft" />
      </section>}</>;
    }
    render(<Fixture />);
    return { collapse, measure(nextWidth: number, nextThreshold = threshold) { width = nextWidth; threshold = nextThreshold; act(() => ResizeProbe.emit()); } };
  }
  it("observes a frame that mounts only after its read finishes", () => {
    const control = setup(700);
    expect(ResizeProbe.active.size).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Toggle loaded frame" }));
    expect(ResizeProbe.active.size).toBe(1);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "false");
    control.measure(1000);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
  });
  it("retains the same draft element and reports its focused pane when capacity shrinks", () => {
    const control = setup(1000);
    fireEvent.click(screen.getByRole("button", { name: "Toggle loaded frame" }));
    const field = screen.getByRole("textbox", { name: "Retained draft" });
    fireEvent.change(field, { target: { value: "Keep my exact reason." } });
    field.focus();
    control.measure(700);
    expect(control.collapse).toHaveBeenCalledWith(field);
    expect(screen.getByRole("textbox", { name: "Retained draft" })).toBe(field);
    expect(field).toHaveValue("Keep my exact reason.");
  });
  it("measures token enlargement and disconnects a removed frame", () => {
    const control = setup(1000);
    fireEvent.click(screen.getByRole("button", { name: "Toggle loaded frame" }));
    control.measure(1000, 1712);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "false");
    fireEvent.click(screen.getByRole("button", { name: "Toggle loaded frame" }));
    expect(ResizeProbe.active.size).toBe(0);
  });
});
