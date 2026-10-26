import { fireEvent, screen, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { useState } from "react";
import { renderWithProviders } from "../test/renderWithProviders";
import { PdfSourceReader } from "./PdfSourceReader";
import type { PdfPageText } from "./usePdfPageText";

vi.mock("./PdfPane", () => ({ PdfPane: ({ onReadText, requestedPage, onObservedPage, onPlaceRequestedPage }: { onReadText?: () => void; requestedPage: number | null; onObservedPage?: (page: number) => void; onPlaceRequestedPage?: (page: number, element: HTMLElement) => void }) => <section>
  <p data-testid="original-page">Original page {requestedPage}</p>
  <button onClick={onReadText}>Read text</button>
  <button onClick={() => onObservedPage?.(3)}>Observe PDF page 3</button>
  <button onClick={(event) => onPlaceRequestedPage?.(requestedPage ?? 1, event.currentTarget)}>Place original page</button>
</section> }));

const result = (page: number, changes: Partial<PdfPageText> = {}): PdfPageText => ({
  check_run_id: 5, manuscript_id: 10, page, page_count: 4, state: "available", reason: null,
  blocks: [{ text: `Saved source page ${page}.\nSecond line.`, is_furniture: false }], saved_block_count: 1, omitted_block_count: 0, ...changes,
});

function Harness({ visible = true, actor = 1 }: { visible?: boolean; actor?: number }) {
  const location = useLocation();
  return <><button>Elsewhere</button><p data-testid="query">{location.search}</p><PdfSourceReader run={5} manuscript={10} actor={actor} visible={visible} fileUrl="/source" regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => undefined} requestedPage={4} /></>;
}

function render(route = "/report/5/document", visible = true) {
  return renderWithProviders(<Harness visible={visible} />, { route });
}

beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const page = Number(new URL(String(input), "http://localhost").searchParams.get("page"));
    return new Response(JSON.stringify(result(page)));
  }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Page text source and explicit focus context", () => {
  it("keeps controlled source presentation local to the finding route", async () => {
    function LocalReader() {
      const location = useLocation();
      const [params, change] = useState(() => new URLSearchParams());
      return <><p data-testid="local-route">{location.pathname}{location.search}</p><PdfSourceReader run={5} manuscript={10} actor={1} visible fileUrl="/source" regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => undefined} requestedPage={4} presentation={{ params, change }} /></>;
    }
    renderWithProviders(<LocalReader />, { route: "/flags/7?return=kept" });
    fireEvent.click(screen.getByRole("button", { name: "Read text" }));
    await screen.findByText(/Saved source page 4/);
    expect(screen.getByTestId("local-route")).toHaveTextContent("/flags/7?return=kept");
    fireEvent.click(screen.getAllByRole("button", { name: "View original PDF p. 4" })[0]);
    expect(screen.getByTestId("original-page")).toHaveTextContent("Original page 4");
    expect(screen.getByTestId("local-route")).toHaveTextContent("/flags/7?return=kept");
  });
  it("uses observed PDF page separately from selected page and returns to current original", async () => {
    render();
    fireEvent.click(screen.getByRole("button", { name: "Observe PDF page 3" }));
    const read = screen.getByRole("button", { name: "Read text" }); read.focus(); fireEvent.click(read);
    await screen.findByText(/Saved source page 3/);
    expect(screen.getByTestId("query")).toHaveTextContent("text_page=3");
    expect(screen.getByText(/Selected source: PDF p. 4. Reading text: PDF p. 3/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).toHaveFocus();
    const original = screen.getAllByRole("button", { name: "View original PDF p. 3" })[0]; original.focus(); fireEvent.click(original);
    expect(screen.getByTestId("original-page")).toHaveTextContent("Original page 3");
    expect(screen.getByTestId("query")).not.toHaveTextContent("page_text");
  });

  it("has distinct selected-source original destination", async () => {
    render("/report/5/document?source=page_text&text_page=3&finding=7");
    await screen.findByText(/Saved source page 3/);
    fireEvent.click(screen.getByRole("button", { name: "Return to selected PDF p. 4" }));
    expect(screen.getByTestId("original-page")).toHaveTextContent("Original page 4");
    expect(screen.getByTestId("query")).toHaveTextContent("finding=7");
  });

  it("does not reuse an observed page from an unmounted PDF instance", async () => {
    render();
    fireEvent.click(screen.getByRole("button", { name: "Observe PDF page 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Read text" }));
    await screen.findByText(/Saved source page 3/);
    fireEvent.click(screen.getByRole("button", { name: "Return to selected PDF p. 4" }));
    expect(screen.getByTestId("original-page")).toHaveTextContent("Original page 4");
    fireEvent.click(screen.getByRole("button", { name: "Read text" }));
    await screen.findByText(/Saved source page 4/);
    expect(screen.queryByText(/Saved source page 3/)).not.toBeInTheDocument();
  });

  it("out-of-range text offers generic original recovery without a false page destination", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result(999, { state: "unavailable", reason: "page_out_of_range", blocks: [], saved_block_count: null, omitted_block_count: null })))));
    render("/report/5/document?source=page_text&text_page=999");
    await screen.findByText("That PDF page is not available");
    expect(screen.queryByRole("button", { name: "View original PDF p. 999" })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "View original PDF" })[0]);
    expect(screen.getByTestId("query")).not.toHaveTextContent("999");
    expect(screen.getByTestId("original-page")).toHaveTextContent("Original page 4");
  });

  it.each([401, 403, 404])("private failure %s leads to the established recovery route", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "denied", message: "denied" } }), { status })));
    render("/report/5/document?source=page_text&text_page=3");
    const link = await screen.findByRole("link", { name: status === 401 ? "Sign in again" : "Return to Review Desk" });
    expect(new URL(link.getAttribute("href")!, "http://localhost").pathname).toBe(status === 401 ? "/signin" : "/dashboard");
  });

  it("restored URL and asynchronous arrival do not create focus intent", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    render("/report/5/document?source=page_text&text_page=3");
    const elsewhere = screen.getByRole("button", { name: "Elsewhere" }); elsewhere.focus();
    await act(async () => resolve(new Response(JSON.stringify(result(3)))));
    await screen.findByText(/Saved source page 3/);
    expect(elsewhere).toHaveFocus();
  });

  it("page action focuses once while loading, arrival never steals focus again", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    render();
    const read = screen.getByRole("button", { name: "Read text" }); read.focus(); fireEvent.click(read);
    expect(screen.getByRole("heading", { level: 2 })).toHaveFocus();
    const elsewhere = screen.getByRole("button", { name: "Elsewhere" }); elsewhere.focus();
    await act(async () => resolve(new Response(JSON.stringify(result(4)))));
    await screen.findByText(/Saved source page 4/);
    expect(elsewhere).toHaveFocus();
  });

  it("invalid committed page stays at current page and does not silently clamp", async () => {
    render("/report/5/document?source=page_text&text_page=3");
    await screen.findByText(/Saved source page 3/);
    const field = screen.getByRole("textbox", { name: "PDF page" });
    fireEvent.change(field, { target: { value: "4.2" } }); fireEvent.blur(field);
    expect(screen.queryByText("Choose a whole PDF page number from 1 to 4.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(field).toHaveFocus(); expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("query")).toHaveTextContent("text_page=3");
    expect(screen.getByText("Choose a whole PDF page number from 1 to 4.")).toBeInTheDocument();
  });

  it("clears old page immediately and rejects a late response for that page", async () => {
    let resolveSecond!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const page = Number(new URL(String(input), "http://localhost").searchParams.get("page"));
      return page === 4 ? new Promise<Response>((done) => { resolveSecond = done; }) : new Response(JSON.stringify(result(3)));
    }));
    render("/report/5/document?source=page_text&text_page=3");
    await screen.findByText(/Saved source page 3/);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.queryByText(/Saved source page 3/)).not.toBeInTheDocument();
    await act(async () => resolveSecond(new Response(JSON.stringify(result(3)))));
    await screen.findByText("Page text could not be loaded");
    expect(screen.queryByText(/Saved source page 3/)).not.toBeInTheDocument();
  });

  it.each(["reading_order_not_recorded", "reading_order_unresolved", "source_binding_unverified"] as const)("refuses uncertain source as a no-text success: %s", async (reason) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result(3, { state: "unavailable", reason, blocks: [], saved_block_count: null, omitted_block_count: null })))));
    render("/report/5/document?source=page_text&text_page=3");
    await waitFor(() => expect(screen.queryByText("Loading saved text for PDF p. 3.")).not.toBeInTheDocument());
    expect(screen.queryByText(/No text was extracted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Saved source page/)).not.toBeInTheDocument();
    expect(screen.getByText(/recorded assessment has not been changed/)).toBeInTheDocument();
  });

  it("scanned page discloses visual content and preserves original recovery", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(result(3, { state: "no_selectable_text", blocks: [], saved_block_count: 0 })))));
    render("/report/5/document?source=page_text&text_page=3");
    await screen.findByText(/No text was extracted from this PDF page/);
    expect(screen.getAllByRole("button", { name: "View original PDF p. 3" })).toHaveLength(2);
    expect(screen.getByText(/Graphics, diagrams, tables, formulas and layout may be absent/)).toBeInTheDocument();
  });

  it("actor changes abort the prior private request and cannot display its late text", async () => {
    let oldReply!: (response: Response) => void;
    let oldSignal: AbortSignal | null | undefined;
    let requests = 0;
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests += 1;
      if (requests === 1) {
        oldSignal = init?.signal;
        return new Promise<Response>((done) => { oldReply = done; });
      }
      return new Response(JSON.stringify(result(3, { blocks: [{ text: "Current actor text", is_furniture: false }] })));
    }));
    function Actors() {
      const [actor, setActor] = useState(1);
      return <><button onClick={() => setActor(2)}>Change actor</button><PdfSourceReader key={actor} run={5} manuscript={10} actor={actor} visible fileUrl="/source" regions={[]} flags={[]} selectedFlagId={null} onSelectFlag={() => undefined} requestedPage={4} /></>;
    }
    renderWithProviders(<Actors />, { route: "/report/5/document?source=page_text&text_page=3" });
    await waitFor(() => expect(requests).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Change actor" }));
    await screen.findByText("Current actor text");
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => oldReply(new Response(JSON.stringify(result(3, { blocks: [{ text: "Previous actor private text", is_furniture: false }] })))));
    expect(screen.queryByText("Previous actor private text")).not.toBeInTheDocument();
    expect(screen.getByText("Current actor text")).toBeInTheDocument();
  });

  it("hidden source and later arrival do not focus; reopening retains reading scroll", async () => {
    function Panes() {
      const [visible, setVisible] = useState(true);
      return <><button onClick={() => setVisible((value) => !value)}>Toggle source</button><Harness visible={visible} /></>;
    }
    renderWithProviders(<Panes />, { route: "/report/5/document?source=page_text&text_page=3" });
    await screen.findByText(/Saved source page 3/);
    const heading = screen.getByRole("heading", { level: 2 });
    const scroll = heading.closest("article")?.parentElement;
    expect(scroll).not.toBeNull();
    scroll!.scrollTop = 240;
    const toggle = screen.getByRole("button", { name: "Toggle source" }); toggle.focus();
    fireEvent.click(toggle); fireEvent.click(toggle);
    expect(toggle).toHaveFocus(); expect(scroll!.scrollTop).toBe(240);
  });

  it.each([401, 403, 404, 410])("private failure %s never exposes text or page-count metadata", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "denied", message: "denied" } }), { status })));
    render("/report/5/document?source=page_text&text_page=3");
    await waitFor(() => expect(screen.queryByText("Loading saved text for PDF p. 3.")).not.toBeInTheDocument());
    expect(screen.queryByText(/Saved source page/)).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Page text controls" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Selected source: PDF/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /View original PDF/ })).not.toBeInTheDocument();
  });
});
