// F8/4w: real route, action, reader and pane; synthetic transport, PDF.js and layout.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { CriterionReviewLayout } from "../report/CriterionReviewScope";
import { clearRouteReturnFocus } from "../routing/useRouteFocus";
import { stubFetchByPath } from "../test/renderWithProviders";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";

const pdfjs = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {}, getDocument: pdfjs.getDocument,
  TextLayer: class { render = async () => {}; cancel = () => {}; },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
class IntersectionStub {
  static instances: IntersectionStub[] = [];
  target?: Element;
  readonly callback: IntersectionObserverCallback;
  readonly options: IntersectionObserverInit;
  constructor(callback: IntersectionObserverCallback, options: IntersectionObserverInit) {
    this.callback = callback; this.options = options; IntersectionStub.instances.push(this);
  }
  observe(target: Element) { this.target = target; }
  unobserve(target: Element) { if (this.target === target) this.target = undefined; }
  disconnect() { this.target = undefined; }
  emit() {
    this.callback([{ target: this.target, intersectionRatio: 1, isIntersecting: true } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver);
  }
}
class ResizeStub {
  static instances: ResizeStub[] = [];
  target?: Element;
  readonly callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; ResizeStub.instances.push(this); }
  observe(target: Element) { this.target = target; }
  unobserve(target: Element) { if (this.target === target) this.target = undefined; }
  disconnect() { this.target = undefined; }
  emit() { this.callback([{ target: this.target, contentRect: { width: 600, height: 800 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
}
const snapshot = "f".repeat(64);
const identity = "d".repeat(64);
const refs = ["a".repeat(64), "b".repeat(64)];
const origin = "/report/5/document?view=decision&pane=review&filter=all&sentinel=kept&criterion=1";
const quotes = ["First recorded excerpt", "Second recorded excerpt"];
let frames = new Map<number, FrameRequestCallback>();
let frameId = 0;
let client: QueryClient | undefined;
beforeEach(() => {
  pdfjs.getDocument.mockReset(); frames = new Map(); frameId = 0;
  IntersectionStub.instances = []; ResizeStub.instances = [];
  vi.stubGlobal("IntersectionObserver", IntersectionStub);
  vi.stubGlobal("ResizeObserver", ResizeStub);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});
afterEach(() => {
  cleanup(); client?.clear(); client = undefined;
  clearRouteReturnFocus("signal-resolution-source-");
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});
function currentFrames() {
  // Never retain or invoke a callback after cancellation or across another frame.
  const ids = [...frames.keys()];
  act(() => ids.forEach(id => {
    const callback = frames.get(id);
    if (callback) { frames.delete(id); callback(0); }
  }));
}
function model() {
  return { numPages: 1, getPage: vi.fn(async () => ({
    rotate: 0, getViewport: () => ({ width: 600, height: 800, convertToViewportPoint: (x: number, y: number) => [x, y] }),
    render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
    getTextContent: async () => ({ items: [] }), cleanup: vi.fn(),
  })) };
}

describe("recorded PDF route ownership", () => {
  it("places delayed same-source B once after returning from A, retaining draft and focus", async () => {
    const heldB = deferred<void>();
    const heldPdf = deferred<ReturnType<typeof model>>();
    const tasks: Array<{ bound: boolean; promise: Promise<ReturnType<typeof model>>; destroy: ReturnType<typeof vi.fn> }> = [];
    pdfjs.getDocument.mockImplementation((options: { data?: Uint8Array }) => {
      const task = { bound: Boolean(options.data), promise: options.data ? heldPdf.promise : Promise.resolve(model()), destroy: vi.fn() };
      tasks.push(task); return task;
    });
    const evidence = quotes.map(quote => ({ quote, anchor: "page 1" }));
    const item = { check_result_id: 1, criterion_id: 1, criterion_text: "Custom criterion", weight: 1,
      votes: ["pass", "fail", "pass"], agreement: 0.667, ai_majority_verdict: "pass", assessment_revision: 0,
      reasoning: "Saved explanation", reason: null, review_reason: "low_confidence", evidence };
    const report = { assessment_revision: 0, check_run_id: 5, manuscript_id: 10,
      manuscript_group_label: "Synthetic route control", manuscript_original_filename: "source.pdf", rubric_title: "Custom format",
      status: "needs_review", composite_score: null, thresholds: {}, reason: null, flag_deduction: 0,
      unresolved_high_flag_count: 0, decision: null, decided_at: null, decision_note: null, pending_review_count: 1,
      rubric_is_current: true, llm_mode: "real", rubric_needs_review: false, rubric_parse_issues: null,
      previous_status: null, previous_composite_score: null, integrity_check_status: [], results: [] };
    const normal = stubFetchByPath({
      "/check-runs/5/document": { manuscript_id: 10, source_format: "pdf", available: true,
        original_filename: "source.pdf", page_count: 1, regions: [] },
      "/check-runs/5/share": null, "/check-runs/5/report": report, "/check-runs/5/flags": [],
      "/check-runs/5/escalated": [item], "/check-runs/5/document/reuse-matches": { matches: [], passage_archive_size_n: 0 },
    });
    const locations: string[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/check-runs/5/evidence") return new Response(JSON.stringify({
        snapshot_id: snapshot, assessment_revision: 0, llm_mode: "real",
        references: evidence.map((row, index) => ({ reference_id: refs[index], criterion_id: 1, flag_id: null,
          role: "criterion_evidence", index, ...row, source_scope: "own_manuscript", source_state: "not_checked" })),
      }));
      const location = url.pathname.match(/\/evidence\/([ab]{64})\/location$/);
      if (location) {
        const ref = location[1]; locations.push(ref);
        if (ref === refs[1]) await heldB.promise;
        return new Response(JSON.stringify({ snapshot_id: snapshot, source_identity: identity, source_format: "pdf",
          caption: ref === refs[0] ? "Recorded location A." : "Recorded location B.",
          placement: { reference_id: ref, state: "text_layer_match", page: 1, paragraph: null,
            boxes: ref === refs[0] ? [[1, 2, 4, 7]] : [[10, 20, 40, 70]], reason: null } }));
      }
      if (url.pathname === "/check-runs/5/evidence/source-file") return new Response(new Uint8Array([1, 2, 3]), {
        headers: { "Content-Type": "application/pdf", "X-Evidence-Source-Identity": identity },
      });
      return normal(input, init);
    });
    vi.stubGlobal("fetch", fetch);
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(ME_QUERY_KEY, { id: 1 });
    const router = createMemoryRouter([{ path: "/report/:checkRunId", element: <CriterionReviewLayout />,
      children: [{ path: "document", element: <SignalDocumentViewerPage /> }] }], {
        initialEntries: [{ pathname: "/report/5/document", search: origin.slice(origin.indexOf("?")), state: { notice: "retained" } }],
      });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    await waitFor(() => expect(document.getElementById("signal-resolution-source-5-1-0")).toBeEnabled());
    const actionA = document.getElementById("signal-resolution-source-5-1-0")!;
    fireEvent.click(screen.getByRole("button", { name: "Meets criterion" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Comment (optional)" }), { target: { value: "Private draft survives both source actions" } });
    const originalSearch = router.state.location.search;
    currentFrames();
    actionA.focus(); fireEvent.click(actionA);
    await waitFor(() => expect(locations).toEqual([refs[0]]));
    await waitFor(() => expect(tasks.filter(task => task.bound)).toHaveLength(1));
    currentFrames();
    expect(document.getElementById("manuscript-panel")).toHaveFocus();
    expect(router.state.location.search).toContain("anchor=page+1");
    expect(router.state.location.search).toContain("pane=manuscript");
    expect(router.state.location.state).toEqual({ notice: "retained" });
    await act(async () => heldPdf.resolve(model()));
    const firstPage = await screen.findByLabelText("Page 1 of 1");
    const root = screen.getByRole("region", { name: "Full manuscript, scrollable" });
    const scrollTo = vi.fn(); root.scrollTo = scrollTo;
    Object.defineProperty(root, "clientWidth", { configurable: true, value: 600 });
    Object.defineProperty(root, "clientHeight", { configurable: true, value: 500 });
    vi.spyOn(root, "getBoundingClientRect").mockReturnValue({ top: 20 } as DOMRect);
    vi.spyOn(firstPage, "getBoundingClientRect").mockReturnValue({ top: 100 } as DOMRect);
    await waitFor(() => expect(IntersectionStub.instances.some(observer => observer.target === firstPage && observer.options.rootMargin)).toBe(true));
    act(() => {
      IntersectionStub.instances.filter(observer => observer.target === firstPage && observer.options.rootMargin).forEach(observer => observer.emit());
      ResizeStub.instances.filter(observer => observer.target === firstPage).forEach(observer => observer.emit());
    });
    await waitFor(() => expect(document.querySelector(`[data-reference-id="${refs[0]}"]`)).not.toBeNull());
    expect(document.getElementById("manuscript-panel")).toHaveFocus();
    expect(frames.size).toBe(1);
    const oldFrame = [...frames.keys()][0];
    const loadedCount = tasks.length;
    const boundTask = tasks.find(task => task.bound)!;
    root.scrollTop = 137;
    const backA = screen.getByRole("button", { name: "Return to criterion review" }); backA.focus(); fireEvent.click(backA);
    await waitFor(() => expect(document.getElementById("signal-resolution-source-5-1-0")).toHaveFocus());
    expect(router.state.location.search).toBe(originalSearch);
    expect(router.state.location.state).toEqual({ notice: "retained" });
    expect(screen.getByRole("textbox", { name: "Comment (optional)" })).toHaveValue("Private draft survives both source actions");
    const actionB = document.getElementById("signal-resolution-source-5-1-1")!;
    expect(actionB).toBeEnabled(); actionB.focus(); fireEvent.click(actionB);
    await waitFor(() => expect(locations).toEqual(refs));
    expect(frames.has(oldFrame)).toBe(false);
    expect(document.querySelectorAll("[data-reference-id]")).toHaveLength(0);
    expect(screen.getByText("Locating the recorded passage in its source copy.")).toBeVisible();
    expect(screen.getByLabelText("Page 1 of 1")).toBe(firstPage);
    expect(screen.getByRole("region", { name: "Full manuscript, scrollable" })).toBe(root);
    expect(root.scrollTop).toBe(137);
    currentFrames();
    expect(scrollTo).not.toHaveBeenCalled();
    expect(scroll.mock.contexts.filter(target => target === firstPage)).toHaveLength(0);
    expect(document.getElementById("manuscript-panel")).toHaveFocus();
    await act(async () => { heldB.resolve(); await heldB.promise; });
    expect(await screen.findByText("Recorded location B.")).toBeVisible();
    await waitFor(() => expect(document.querySelector(`[data-reference-id="${refs[1]}"]`)).not.toBeNull());
    expect(document.querySelector(`[data-reference-id="${refs[0]}"]`)).toBeNull();
    await waitFor(() => expect(frames.size).toBe(1));
    currentFrames();
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo).toHaveBeenCalledWith({ top: 217, behavior: "auto" });
    expect(scroll.mock.contexts.filter(target => target === firstPage)).toHaveLength(1);
    currentFrames();
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Page 1 of 1")).toBe(firstPage);
    expect(tasks).toHaveLength(loadedCount); expect(boundTask.destroy).not.toHaveBeenCalled();
    expect(fetch.mock.calls.filter(([input]) => String(input).includes("/evidence/source-file?"))).toHaveLength(1);
    const backB = screen.getByRole("button", { name: "Return to criterion review" }); backB.focus(); fireEvent.click(backB);
    await waitFor(() => expect(document.getElementById("signal-resolution-source-5-1-1")).toHaveFocus());
    expect(screen.getByRole("textbox", { name: "Comment (optional)" })).toHaveValue("Private draft survives both source actions");
    expect(router.state.location.search).toBe(originalSearch);
    expect(router.state.location.state).toEqual({ notice: "retained" });
    expect(router.state.location.search).toContain("sentinel=kept");
    expect(router.state.location.search).toContain("criterion=1");
    expect(fetch.mock.calls.filter(([, init]) => /^(POST|PUT|PATCH|DELETE)$/i.test(init?.method ?? "GET"))).toHaveLength(0);
  });
});
