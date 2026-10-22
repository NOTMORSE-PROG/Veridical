import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FlagRegionOut, FlagSummaryOut } from "../api/types";
import { PdfPane } from "./PdfPane";

const mocks = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {}, getDocument: mocks.getDocument,
  TextLayer: class { render() { return Promise.resolve(); } cancel() {} },
}));

const lead: FlagSummaryOut = {
  id: 41, check_kind: "originality_reuse", affects_readiness: false, severity: "high",
  criterion_text: null, evidence_excerpt: "Saved passage", page_anchor: "page 1", overridden: false,
  is_passage_level: true, first_upload_context: false, confirmed_citation_source: false,
  problem_kind: "reuse_high_similarity",
};
const region: FlagRegionOut = { flag_id: 41, kind: "page_bbox", page: 1, end_page: null,
  bbox: [1, 1, 30, 30], all_bboxes: [], paragraph: null, index: null };
let resize: ResizeObserverCallback;
let finishRender: () => void;
let renderStarted: number;
let delayed: boolean;

function tree(metadata: boolean, focusSelection = true) {
  return <><h2 tabIndex={-1}>Selected review</h2><button>Later control</button><PdfPane
    fileUrl="selection.pdf" flags={[lead]} regions={metadata ? [region] : []}
    selectedFlagId={41} focusSelection={focusSelection} requestedPage={null} onSelectFlag={vi.fn()} /></>;
}
const marker = () => screen.getByRole("button", { name: /Record #41:.*Exact passage/ });
async function loaded() {
  await screen.findByLabelText("Page 1 of 1");
  await waitFor(() => expect(renderStarted).toBeGreaterThan(0));
  await act(async () => {});
}
function resized() {
  resize([{ contentRect: { width: 500 } } as ResizeObserverEntry], {} as ResizeObserver);
}

describe("owned PDF source selection", () => {
  beforeEach(() => {
    renderStarted = 0; delayed = false;
    const held = new Promise<void>((resolve) => { finishRender = resolve; });
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { resize = callback; }
      observe() { resize([{ contentRect: { width: 600 } } as ResizeObserverEntry], {} as ResizeObserver); }
      disconnect() {}
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const viewport = { width: 600, height: 800, convertToViewportPoint: (x: number, y: number) => [x, y] };
    mocks.getDocument.mockReturnValue({ promise: Promise.resolve({ numPages: 1,
      getPage: async () => ({ rotate: 0, getViewport: () => ({ ...viewport }),
        render: () => { renderStarted++; return { promise: delayed ? held : Promise.resolve(), cancel() {} }; },
        getTextContent: async () => ({ items: [] }), cleanup() {},
      }),
    }), destroy: vi.fn() });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("keeps review focus while rendering its selected source highlight", async () => {
    render(tree(true, false));
    const heading = screen.getByRole("heading", { name: "Selected review" }); heading.focus();
    await screen.findByRole("button", { name: /Record #41:.*Exact passage/ });
    expect(heading).toHaveFocus();
    expect(marker()).toHaveAttribute("aria-pressed", "true");
  });

  it("completes an explicit source selection once", async () => {
    render(tree(true)); await loaded();
    await waitFor(() => expect(marker()).toHaveFocus());
    const later = screen.getByRole("button", { name: "Later control" }); later.focus();
    await act(async () => resized());
    expect(later).toHaveFocus();
  });

  it.each(["focus", "Tab", "wheel", "ArrowDown", "blur"])("retires delayed placement after newer %s intent", async (intent) => {
    delayed = true; render(tree(true)); await loaded();
    const later = screen.getByRole("button", { name: "Later control" });
    if (intent === "focus") later.focus();
    else if (intent === "wheel" || intent === "blur") window.dispatchEvent(new Event(intent));
    else document.dispatchEvent(new KeyboardEvent("keydown", { key: intent }));
    await act(async () => finishRender());
    await screen.findByRole("button", { name: /Record #41:.*Exact passage/ });
    expect(marker()).not.toHaveFocus();
    if (intent === "focus") expect(later).toHaveFocus();
  });

  it("does not renew a retired claim when source coordinates arrive later", async () => {
    const { rerender } = render(tree(false)); await loaded();
    const later = screen.getByRole("button", { name: "Later control" }); later.focus();
    rerender(tree(true));
    await screen.findByRole("button", { name: /Record #41:.*Exact passage/ });
    expect(later).toHaveFocus();
  });

  it("can complete a still-owned claim when source coordinates arrive later", async () => {
    const { rerender } = render(tree(false)); await loaded(); rerender(tree(true));
    await waitFor(() => expect(marker()).toHaveFocus());
  });

  it("does not renew consumed placement when source coordinates are replaced", async () => {
    const { rerender } = render(tree(true)); await loaded();
    await waitFor(() => expect(marker()).toHaveFocus());
    const later = screen.getByRole("button", { name: "Later control" }); later.focus();
    rerender(tree(false)); rerender(tree(true));
    await act(async () => resized());
    expect(later).toHaveFocus();
  });
});
