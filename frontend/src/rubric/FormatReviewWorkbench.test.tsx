import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { FormatReviewWorkbench } from "./FormatReviewWorkbench";

class ResizeStub {
  static instances: ResizeStub[] = [];
  target?: Element;
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; ResizeStub.instances.push(this); }
  observe(target: Element) { this.target = target; }
  disconnect() { this.target = undefined; }
  emit() { this.callback([], this as unknown as ResizeObserver); }
}

const version = { id: 5, version: 2, rubric_family_id: "exact-family" };

describe("format comparison workbench", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  function setup(startWidth: number) {
    let width = startWidth;
    ResizeStub.instances = [];
    vi.stubGlobal("ResizeObserver", ResizeStub);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { width: this.classList.contains("signal-format-workbench__threshold") ? 824 : width } as DOMRect;
    });
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": { rubric_id: 5, rubric_family_id: "exact-family", version: 2,
      available: true, source_format: "docx", source_sha256: "a".repeat(64), unavailable_reason: null, reconstruction: "fresh_local_source" },
      "/rubrics/5/document/paragraphs": { rubric_id: 5, rubric_family_id: "exact-family", version: 2,
        available: true, source_format: "docx", source_sha256: "a".repeat(64), unavailable_reason: null, reconstruction: "fresh_local_source",
        content: { paragraphs: [{ paragraph: 0, text: "Actual required format", heading_level: null }], tables: [], images: [], native_math: { state: "complete", occurrence_count: 0, containers: [] } } } }));
    renderWithProviders(<FormatReviewWorkbench rubric={version} onUploadNewVersion={vi.fn()}>
      <label>Unfinished criterion<textarea defaultValue="Old wording" /></label>
    </FormatReviewWorkbench>, { authInstructorId: 1 });
    return (nextWidth: number) => {
      width = nextWidth;
      act(() => ResizeStub.instances.find((item) => item.target?.classList.contains("signal-format-workbench"))!.emit());
    };
  }

  it("keeps source position and exact unsaved criterion text when a desktop split becomes tabs", async () => {
    const resize = setup(1100);
    const source = await screen.findByRole("region", { name: "Original format, scrollable" });
    source.scrollTop = 211;
    const field = screen.getByRole("textbox");
    fireEvent.change(field, { target: { value: "  Corrected requirement\n\nwith retained spacing  " } });
    resize(700);
    const tabs = screen.getByRole("tablist", { name: "Format review views" });
    expect(within(tabs).getByRole("tab", { name: "Criteria" })).toHaveAttribute("aria-selected", "true");
    expect(source).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Read original format" }));
    expect(source).toBeVisible();
    expect(source.scrollTop).toBe(211);
    expect(field).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Back to criteria" }));
    expect(field).toBeVisible();
    expect(field).toHaveValue("  Corrected requirement\n\nwith retained spacing  ");
    resize(1000);
    expect(source).toBeVisible();
    expect(screen.getByRole("textbox")).toBe(field);
    expect(source.scrollTop).toBe(211);
  });

  it("moves tab focus without activating or loading the source until deliberately selected", async () => {
    setup(700);
    const criteria = screen.getByRole("tab", { name: "Criteria" });
    criteria.focus();
    fireEvent.keyDown(criteria, { key: "ArrowRight" });
    const source = screen.getByRole("tab", { name: "Original format" });
    expect(source).toHaveFocus();
    expect(source).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("textbox")).toBeVisible();
    fireEvent.click(source);
    await waitFor(() => expect(screen.getByText("Actual required format")).toBeVisible());
    expect(source).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Back to criteria" })).toBeInTheDocument();
  });

  it("keeps the focused source pane visible when a wide desktop becomes compact", async () => {
    const resize = setup(1100);
    const source = await screen.findByRole("region", { name: "Original format, scrollable" });
    act(() => source.focus());
    resize(700);
    expect(source).toHaveFocus();
    expect(source).toBeVisible();
    expect(screen.getByRole("tab", { name: "Original format" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps a focused criterion visible even when the earlier compact selection was the source", async () => {
    const resize = setup(700);
    fireEvent.click(screen.getByRole("tab", { name: "Original format" }));
    await screen.findByText("Actual required format");
    resize(1100);
    const field = screen.getByRole("textbox");
    act(() => field.focus());
    resize(700);
    expect(field).toBeVisible();
    expect(field).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Criteria" })).toHaveAttribute("aria-selected", "true");
  });

  it("returns sequential tab entry to the active view after an inactive tab was explored", () => {
    setup(700);
    const criteria = screen.getByRole("tab", { name: "Criteria" });
    act(() => criteria.focus());
    fireEvent.keyDown(criteria, { key: "ArrowRight" });
    const source = screen.getByRole("tab", { name: "Original format" });
    expect(source).toHaveFocus();
    act(() => screen.getByRole("textbox").focus());
    expect(criteria).toHaveAttribute("tabindex", "0");
    expect(source).toHaveAttribute("tabindex", "-1");
    expect(criteria).toHaveAttribute("aria-selected", "true");
  });

  it("reuses the verified immutable source instead of competing native reads on ordinary tab return", async () => {
    setup(700);
    fireEvent.click(screen.getByRole("tab", { name: "Original format" }));
    await screen.findByText("Actual required format");
    const calls = vi.mocked(fetch).mock.calls.length;
    fireEvent.click(screen.getByRole("tab", { name: "Criteria" }));
    fireEvent.click(screen.getByRole("tab", { name: "Original format" }));
    expect(screen.getByText("Actual required format")).toBeVisible();
    await act(async () => { await Promise.resolve(); });
    expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hands focus to the original-format panel when its source command hides the initiating control", async () => {
    setup(700);
    const trigger = screen.getByRole("button", { name: "Read original format" });
    act(() => trigger.focus());
    fireEvent.click(trigger);
    const panel = screen.getByRole("tabpanel", { name: "Original format" });
    expect(panel).toHaveFocus();
    expect(panel).toBeVisible();
    expect(trigger).not.toBeVisible();
    await screen.findByText("Actual required format");
  });
});
