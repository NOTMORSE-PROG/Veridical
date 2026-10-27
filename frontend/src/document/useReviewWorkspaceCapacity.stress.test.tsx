import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useReviewWorkspaceCapacity } from "./useReviewWorkspaceCapacity";

class ResizeProbe {
  static active = new Set<ResizeProbe>();
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    ResizeProbe.active.add(this);
  }
  observe() {}
  disconnect() {
    ResizeProbe.active.delete(this);
  }
  static emit() {
    for (const observer of [...this.active]) observer.callback([], observer as unknown as ResizeObserver);
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  ResizeProbe.active.clear();
});

describe("Adversarial stress testing of Review Workspace capacity and desktop layout", () => {
  function setupStressFixture(initialWidth: number, initialThreshold = 856) {
    let width = initialWidth;
    let threshold = initialThreshold;

    vi.stubGlobal("ResizeObserver", ResizeProbe);
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.dataset.testid === "frame" ? width : 0;
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { width: this.classList.contains("signal-review-workspace-threshold") ? threshold : width } as DOMRect;
    });

    const collapse = vi.fn();
    const expand = vi.fn();

    function Fixture() {
      const [mounted, setMounted] = useState(true);
      const workspace = useReviewWorkspaceCapacity(collapse, expand);

      return (
        <div>
          <button onClick={() => setMounted((m) => !m)}>Toggle frame</button>
          {mounted && (
            <section ref={workspace.frame} data-testid="frame" data-split={workspace.split}>
              <span ref={workspace.threshold} className="signal-review-workspace-threshold" />
              <input aria-label="Criterion draft textarea" defaultValue="Initial analysis" />
              <button aria-label="Submit judgment">Submit</button>
            </section>
          )}
        </div>
      );
    }

    render(<Fixture />);
    return {
      collapse,
      expand,
      resize(nextWidth: number, nextThreshold = threshold) {
        width = nextWidth;
        threshold = nextThreshold;
        act(() => ResizeProbe.emit());
      },
    };
  }

  it("sustains split layout at standard desktop resolutions (1024px and 1440px)", () => {
    // 1024px desktop resolution
    const probe = setupStressFixture(1024);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");

    // 1440px wide desktop resolution
    probe.resize(1440);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
    expect(probe.collapse).not.toHaveBeenCalled();
  });

  it("tests 1px threshold boundaries: 855px (stacked), 856px (split), 857px (split)", () => {
    const probe = setupStressFixture(1024, 856);
    const draftInput = screen.getByRole("textbox", { name: "Criterion draft textarea" });
    draftInput.focus();

    // Just 1px below threshold -> collapse
    probe.resize(855, 856);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "false");
    expect(probe.collapse).toHaveBeenCalledWith(draftInput);

    // Exact threshold match (856px) -> expand
    probe.resize(856, 856);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
    expect(probe.expand).toHaveBeenCalledWith(draftInput);

    // 1px above threshold (857px) -> remains split
    probe.resize(857, 856);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
  });

  it("tolerates zero or negative element dimensions without crashing or toggling split", () => {
    const probe = setupStressFixture(1024, 856);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");

    // Zero available width (e.g. unmounted or hidden container)
    probe.resize(0, 856);
    // Should gracefully skip and maintain current split status without error
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");

    // Zero threshold width
    probe.resize(1024, 0);
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
  });

  it("handles high-frequency resize oscillations without losing focused draft", () => {
    const probe = setupStressFixture(1024, 856);
    const draftInput = screen.getByRole("textbox", { name: "Criterion draft textarea" });
    fireEvent.change(draftInput, { target: { value: "Draft with pending instructor edits." } });
    draftInput.focus();

    // Rapid oscillation across threshold
    for (let i = 0; i < 10; i++) {
      probe.resize(i % 2 === 0 ? 700 : 1200, 856);
    }

    // Final state is 1200px (split = true)
    expect(screen.getByTestId("frame")).toHaveAttribute("data-split", "true");
    expect(draftInput).toHaveValue("Draft with pending instructor edits.");
    expect(screen.getByRole("textbox", { name: "Criterion draft textarea" })).toBe(draftInput);
  });
});
