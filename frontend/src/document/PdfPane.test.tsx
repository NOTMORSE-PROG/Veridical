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

const props = {
  regions: [], flags: [], selectedFlagId: null, onSelectFlag: vi.fn(), requestedPage: null,
};

describe("PdfPane page visibility", () => {
  beforeEach(() => {
    ObserverStub.instances = [];
    vi.stubGlobal("IntersectionObserver", ObserverStub);
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

  it("keeps optional page-scope controls tied to complete-collection identities", async () => {
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
    const control = await screen.findByRole("button", { name: /Comparison lead 3 of 31\. Record #41/ });
    expect(control).toHaveTextContent("L3");
    expect(control).not.toHaveAccessibleName(/High severity|Finding 1/);
    fireEvent.click(control);
    expect(onSelectFlag).toHaveBeenCalledWith(41);
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
});
