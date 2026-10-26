import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PdfPane } from "./PdfPane";
import type { FlagSummaryOut } from "../api/types";
import { comparisonLeadSnapshot } from "../domain/comparisonLeadIdentity";

const mocks = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument: mocks.getDocument }));

class ObserverStub {
  static instances: ObserverStub[] = [];
  target?: Element;
  readonly callback: IntersectionObserverCallback;
  readonly options: IntersectionObserverInit;
  constructor(callback: IntersectionObserverCallback, options: IntersectionObserverInit) {
    this.callback = callback;
    this.options = options;
    ObserverStub.instances.push(this);
  }
  observe(target: Element) { this.target = target; }
  disconnect() { this.target = undefined; }
  emit(ratios: number[]) {
    this.callback(ratios.map((intersectionRatio) => ({
      target: this.target, intersectionRatio, isIntersecting: intersectionRatio > 0,
    } as IntersectionObserverEntry)), this as unknown as IntersectionObserver);
  }
}

function observer(page: number, near = false) {
  const found = ObserverStub.instances.find((entry) =>
    entry.target?.getAttribute("data-page") === String(page) && Boolean(entry.options.rootMargin) === near);
  if (!found) throw new Error(`Observer for page ${page} not registered`);
  return found;
}

class ResizeStub {
  static instances: ResizeStub[] = [];
  target?: Element;
  readonly callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; ResizeStub.instances.push(this); }
  observe(target: Element) { this.target = target; }
  disconnect() { this.target = undefined; }
  emit() { this.callback([], this as unknown as ResizeObserver); }
}

function installGeometry(root: HTMLElement) {
  let width = 600;
  Object.defineProperty(root, "clientWidth", { configurable: true, get: () => width });
  Object.defineProperty(root, "clientHeight", { configurable: true, value: 500 });
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(() => ({
    top: 100, left: 50, right: 50 + width, bottom: 600, height: 500, width,
  }) as DOMRect);
  const pages = Array.from(root.querySelectorAll<HTMLElement>("[data-page]"));
  for (const [index, page] of pages.entries()) {
    vi.spyOn(page, "getBoundingClientRect").mockImplementation(() => {
      const zoom = Number.parseFloat(page.parentElement!.style.inlineSize) / 100;
      // Different page heights and fixed gaps prevent an absolute scroll-ratio
      // shortcut from satisfying the source-point contract.
      const heights = pages.map((_, i) => (i === 1 ? 400 : 800) * width / 600 * zoom);
      const top = 100 + 20 + heights.slice(0, index).reduce((sum, h) => sum + h, 0) + index * 20 - root.scrollTop;
      const left = 50 - root.scrollLeft;
      return { top, bottom: top + heights[index], left, right: left + width * zoom, width: width * zoom, height: heights[index] } as DOMRect;
    });
  }
  root.scrollTo = vi.fn((value?: ScrollToOptions | number, y?: number) => {
    const options = typeof value === "number" ? { left: value, top: y } : value ?? {};
    root.scrollTop = Math.max(0, options.top ?? root.scrollTop);
    root.scrollLeft = Math.max(0, options.left ?? root.scrollLeft);
  });
  const resize = ResizeStub.instances.find((entry) => entry.target === root)!;
  act(() => resize.emit());
  return { pages, resize: (next: number) => { width = next; act(() => resize.emit()); }, notify: () => act(() => resize.emit()) };
}

const props = {
  regions: [], flags: [], selectedFlagId: null, onSelectFlag: vi.fn(), requestedPage: null,
};

describe("PdfPane page visibility", () => {
  beforeEach(() => {
    ObserverStub.instances = [];
    vi.stubGlobal("IntersectionObserver", ObserverStub);
    ResizeStub.instances = [];
    vi.stubGlobal("ResizeObserver", ResizeStub);
    mocks.getDocument.mockImplementation(({ url }: { url: string }) => ({
      promise: Promise.resolve({
        numPages: url === "short.pdf" ? 2 : 29,
        getPage: async () => ({ rotate: 0, getViewport: () => ({ width: 600, height: 800 }) }),
      }),
      destroy: vi.fn(),
    }));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("does not duplicate the review queue with page-scope finding chips", async () => {
    const leads: FlagSummaryOut[] = Array.from({ length: 31 }, (_, index) => ({
      id: index + 39, check_kind: "originality_reuse", affects_readiness: false,
      severity: "high", criterion_text: null, evidence_excerpt: "Saved passage",
      page_anchor: "page 1", overridden: false, is_passage_level: false,
      first_upload_context: false, confirmed_citation_source: false, problem_kind: "reuse_high_similarity",
    }));
    const onSelectFlag = vi.fn();
    render(<PdfPane {...props} fileUrl="short.pdf" flags={[leads[2]]}
      comparisonLeads={comparisonLeadSnapshot(21, leads)} onSelectFlag={onSelectFlag}
      regions={[{ flag_id: 41, kind: "page_only", page: 1, end_page: null, bbox: null,
        all_bboxes: [], paragraph: null, index: null }]} />);
    await screen.findByLabelText("Page 2 of 2");
    expect(screen.queryByRole("group", { name: "Findings without an exact passage location" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Comparison lead 3 of 31\. Record #41/ })).not.toBeInTheDocument();
    expect(document.querySelector(".signal-document-scope-markers")).toBeNull();
    expect(onSelectFlag).not.toHaveBeenCalled();
  });

  it("does not clamp an unavailable recorded source page to the last page", async () => {
    render(<PdfPane {...props} fileUrl="short.pdf" requestedPage={999} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The recorded page is not available in this PDF");
    expect(screen.getByRole("spinbutton", { name: "Go to page" })).toHaveValue(1);
  });

  it("does not discard a queued exit or let the old page overwrite the visible page", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    await waitFor(() => observer(29));
    act(() => observer(10).emit([0.75]));
    act(() => observer(29).emit([0.5]));
    act(() => observer(10).emit([0.76, 0]));
    expect(screen.getByText("Page 29 of 29", { selector: "output" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Go to page" })).toHaveValue(29);
  });

  it("uses the latest near-render entry rather than the first entry", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    const page = await screen.findByLabelText("Page 29 of 29");
    await waitFor(() => observer(29, true));
    act(() => observer(29, true).emit([1, 0]));
    expect(within(page).queryByRole("img")).not.toBeInTheDocument();
    act(() => observer(29, true).emit([0, 1]));
    expect(within(page).getByRole("img")).toBeInTheDocument();
  });

  it("clears old visibility when a different document replaces the manuscript", async () => {
    const { rerender } = render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    // DOM insertion can precede the passive effect registering visibility.
    await waitFor(() => observer(29));
    act(() => observer(29).emit([1]));
    rerender(<PdfPane {...props} fileUrl="short.pdf" />);
    await screen.findByLabelText("Page 2 of 2");
    await waitFor(() => observer(1));
    act(() => observer(1).emit([0.5]));
    await waitFor(() => expect(screen.getByRole("spinbutton")).toHaveValue(1));
    expect(screen.getByText("Page 1 of 2", { selector: "output" })).toBeInTheDocument();
  });

  it("scrolls the registered source page within the pane", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    const page = await screen.findByLabelText("Page 29 of 29");
    await waitFor(() => observer(29));
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    root.scrollTo = vi.fn();
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue({ top: 100 } as DOMRect);
    vi.spyOn(page, "getBoundingClientRect").mockReturnValue({ top: 2500 } as DOMRect);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "29" } });
    fireEvent.click(screen.getByRole("button", { name: "Go to entered page" }));
    expect(root.scrollTo).toHaveBeenCalledWith({ top: 2400, behavior: "smooth" });
  });

  it("keeps page 3 and its source point through zoom rather than showing the preceding blank tail", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    const geometry = installGeometry(root);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Go to entered page" }));
    expect(geometry.pages[2].getBoundingClientRect().top).toBe(100);
    const zoom = screen.getByRole("button", { name: "Zoom in" });
    zoom.focus();
    fireEvent.click(zoom);
    expect(geometry.pages[2].getBoundingClientRect().top).toBeCloseTo(100);
    expect(screen.getByRole("spinbutton")).toHaveValue(3);
    expect(zoom).toHaveFocus();
    // A queued observer entry from the old layout cannot overwrite live geometry.
    act(() => observer(2).emit([1]));
    expect(screen.getByRole("spinbutton")).toHaveValue(3);
  });

  it("preserves within-page and horizontal reading points through zoom, fit and resize", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    const geometry = installGeometry(root);
    root.scrollTop = 1260 + 400;
    fireEvent.scroll(root);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(geometry.pages[2].getBoundingClientRect().top + geometry.pages[2].getBoundingClientRect().height / 2).toBeCloseTo(100);
    root.scrollLeft = 90;
    fireEvent.scroll(root);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(root.scrollLeft).toBeCloseTo(105);
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(root.scrollLeft).toBeCloseTo(90);
    fireEvent.click(screen.getByRole("button", { name: "Fit width" }));
    expect(root.scrollTop).toBeCloseTo(1660);
    geometry.resize(450);
    expect(geometry.pages[2].getBoundingClientRect().top + geometry.pages[2].getBoundingClientRect().height / 2).toBeCloseTo(100);
    geometry.resize(750);
    expect(geometry.pages[2].getBoundingClientRect().top + geometry.pages[2].getBoundingClientRect().height / 2).toBeCloseTo(100);
  });

  it("lets a new reading intent replace the stable resize anchor and never replays a late render", async () => {
    render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    const geometry = installGeometry(root);
    root.scrollTop = 1660;
    fireEvent.scroll(root);
    fireEvent.wheel(root);
    root.scrollTop = 2200;
    fireEvent.scroll(root);
    geometry.resize(450);
    const position = root.scrollTop;
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Go to entered page" }));
    geometry.notify();
    act(() => observer(29, true).emit([1]));
    expect(root.scrollTop).toBe(20);
    expect(screen.getByRole("spinbutton")).toHaveValue(1);
    expect(position).toBeGreaterThan(0);
  });

  it("retires queued source-page placement when newer wheel or page-Go intent occurs", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.push(callback); return frames.length; });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const { rerender } = render(<PdfPane {...props} fileUrl="long.pdf" />);
    await screen.findByLabelText("Page 29 of 29");
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    installGeometry(root);
    rerender(<PdfPane {...props} fileUrl="long.pdf" requestedPage={3} />);
    fireEvent.wheel(root);
    act(() => frames.splice(0).forEach((callback) => callback(0)));
    expect(root.scrollTo).not.toHaveBeenCalled();
    rerender(<PdfPane {...props} fileUrl="long.pdf" requestedPage={4} />);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Go to entered page" }));
    act(() => frames.splice(0).forEach((callback) => callback(0)));
    expect(root.scrollTo).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("spinbutton")).toHaveValue(2);
  });

  it("retries a failed original-format PDF without changing document identity", async () => {
    mocks.getDocument.mockImplementationOnce(() => ({ promise: Promise.reject(new Error("Temporary source read failure")), destroy: vi.fn() }));
    render(<PdfPane {...props} fileUrl="short.pdf" documentKind="required_format" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your prepared criteria remain available");
    expect(screen.getByText("Original PDF · Preview unavailable")).toBeInTheDocument();
    expect(screen.getByText("Page navigation unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/Loading pages|Loading page navigation|Page 1 of/)).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Original format, scrollable" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Rendered page 1 of the required format" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps navigation unavailable when the PDF opens but its page metrics fail", async () => {
    mocks.getDocument.mockImplementationOnce(() => ({
      promise: Promise.resolve({ numPages: 2, getPage: async () => { throw new Error("Page metadata unavailable"); } }),
      destroy: vi.fn(),
    }));
    render(<PdfPane {...props} fileUrl="short.pdf" documentKind="required_format" />);
    await screen.findByRole("alert");
    expect(screen.getByText("Original PDF · Preview unavailable")).toBeInTheDocument();
    expect(screen.getByText("Page navigation unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).toBeEnabled();
  });
});
