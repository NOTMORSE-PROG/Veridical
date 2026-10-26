import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PdfPane } from "./PdfPane";

const pdfMock = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {}, getDocument: pdfMock.getDocument,
  TextLayer: class {
    container: HTMLElement;
    constructor({ container }: { container: HTMLElement }) { this.container = container; }
    render() { this.container.textContent = "Stored source text"; return Promise.resolve(); }
    cancel() {}
  },
}));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("retires an interrupted page request when a mounted reader loads another document with the same page count", async () => {
  const frames = new Map<number, FrameRequestCallback>(); let frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("ResizeObserver", class {
    callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) { this.callback = callback; }
    observe(target: Element) { this.callback([{ target, contentRect: { width: 600 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
    disconnect() {}
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    return new DOMRect(0, this.dataset.page === "1" ? -800 : 0, 600, 800);
  });
  const ready = new Map<string, (value: unknown) => void>();
  const page = { rotate: 0, getViewport: () => ({ width: 600, height: 800, convertToViewportPoint: (x: number, y: number) => [x, y] }), render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }), getTextContent: async () => ({}), cleanup: vi.fn() };
  const document = () => ({ numPages: 2, getPage: async () => page });
  pdfMock.getDocument.mockImplementation(({ url }: { url: string }) => ({ promise: new Promise(resolve => ready.set(url, resolve)), destroy: vi.fn() }));
  const props = { regions: [], flags: [], selectedFlagId: null, onSelectFlag: vi.fn(), isVisible: true };
  const view = render(<PdfPane {...props} fileUrl="/source-a.pdf" requestedPage={2} />);
  await act(async () => { ready.get("/source-a.pdf")!(document()); });
  await waitFor(() => expect(view.container.querySelectorAll("[data-page]")).toHaveLength(2));
  async function flushFrames() {
    await act(async () => { const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(performance.now()); });
  }
  // Apply A's first request frame, then replace its source before settlement.
  await flushFrames();
  view.rerender(<PdfPane {...props} fileUrl="/source-b.pdf" requestedPage={1} />);
  await act(async () => { ready.get("/source-b.pdf")!(document()); });
  await waitFor(() => expect(view.container.querySelectorAll("[data-page]")).toHaveLength(2));
  await flushFrames(); await flushFrames();
  expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
  // Visibility tracking must work after B settles, rather than remain locked
  // behind A's cancelled request. The mocked viewport currently contains page2.
  fireEvent.scroll(screen.getByRole("region", { name: "Full manuscript, scrollable" }));
  expect(screen.getByText("Page 2 of 2")).toBeInTheDocument();
});
