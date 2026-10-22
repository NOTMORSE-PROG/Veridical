import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryItemOut, ManuscriptViewerOut } from "../api/types";
import { SignalLibraryPage } from "./SignalLibrary";
import { SignalLibraryDetailPage } from "./SignalLibraryDetail";
import { librarySourceDestination } from "../routing/libraryReturn";

vi.mock("./LibraryContentPane", () => ({ LibraryContentPane: () => <p>Original source</p> }));

const OWN: LibraryItemOut = {
  manuscript_id: 1, is_own: true, label: "Own source", title: "Own source",
  team_name: null, authors: [], program: "IT", school_year: null,
  original_filename: "own.pdf", status: "active", source: "library_submission",
  added_at: "2026-10-01T02:00:00Z", created_at: "2026-10-01T02:00:00Z",
  proposed_fields: [], possible_duplicate_of: null, failure_detail: null,
};
const SHARED: LibraryItemOut = {
  ...OWN, manuscript_id: 2, is_own: false, label: "Library record #2",
  title: null, original_filename: null, source: null,
};
const VIEWER: ManuscriptViewerOut = {
  manuscript_id: 1, original_filename: "own.pdf", source_format: "pdf",
  available: true, unavailable_reason: null, purged_at: null, page_count: 6, regions: [],
};
const ORIGIN = "/library?q=%23source%2B%26%25&program=IT&page=2";

function setup(route = ORIGIN, state?: Record<string, unknown>, unavailable = false, entries?: string[]) {
  const control: { holdFiltered?: Promise<void>; filteredItems?: LibraryItemOut[] } = {};
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    let data: unknown;
    if (url.pathname === "/programs") data = [{ id: 1, name: "IT" }, { id: 2, name: "CS" }];
    else if (url.pathname === "/library") {
      if (url.searchParams.has("search")) await control.holdFiltered;
      const items = url.searchParams.has("search") ? control.filteredItems ?? [OWN, SHARED] : [OWN, SHARED];
      data = url.searchParams.get("scope") === "pending"
        ? { items: [], page: 1, page_size: 50, total: 0 }
        : { items, page: Number(url.searchParams.get("page") ?? 1), page_size: 50, total: items.length ? 60 : 0 };
    } else if (url.pathname.endsWith("/document")) data = VIEWER;
    else if (url.pathname === "/library/1" || url.pathname === "/library/2") {
      if (unavailable) return new Response(JSON.stringify({ error: { code: "not_found", message: "Unavailable" } }), { status: 404 });
      data = url.pathname.endsWith("/1") ? OWN : SHARED;
    } else return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([
    { path: "/library", element: <SignalLibraryPage /> },
    { path: "/library/:manuscriptId", element: <SignalLibraryDetailPage /> },
  ], { initialEntries: entries ?? [state ? { pathname: route.split("?")[0], search: route.includes("?") ? "?" + route.split("?")[1] : "", state } : route], initialIndex: entries ? entries.length - 1 : 0 });
  render(<StrictMode><QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider></StrictMode>);
  return { router, queryClient, control };
}

function sourceRoute(comparisonMode = false, comparisonSelection: number[] = []) {
  return librarySourceDestination(1, { pathname: "/library", search: ORIGIN.slice("/library".length) }, {
    comparisonMode, comparisonSelection,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("Library source return context", () => {
  it.each(["Own source", "Library record #2"])("retains exact filters/page and focuses the originating %s action", async (name) => {
    const { router } = setup();
    const source = await screen.findByRole("link", { name: "View manuscript: " + name });
    fireEvent.click(source);
    await screen.findByRole("heading", { name, level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    const returned = await screen.findByRole("link", { name: "View manuscript: " + name });
    expect(router.state.location.pathname + router.state.location.search).toBe(ORIGIN);
    expect(screen.getByRole("searchbox", { name: "Search Library" })).toHaveValue("#source+&%");
    expect(screen.getByRole("combobox", { name: "Program" })).toHaveValue("IT");
    await waitFor(() => expect(returned).toHaveFocus());
  });

  it.each([0, 1, 2])("retains explicit comparison mode and %i selected records", async (count) => {
    setup();
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    fireEvent.click(screen.getByRole("button", { name: "Compare two records" }));
    for (const box of screen.getAllByRole("checkbox").slice(0, count)) fireEvent.click(box);
    fireEvent.click(screen.getByRole("link", { name: "View manuscript: Own source" }));
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(screen.getByRole("button", { name: "Exit comparison" })).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").filter((box) => (box as HTMLInputElement).checked)).toHaveLength(count);
  });

  it("rearms return focus through Back, Forward, then Back", async () => {
    const { router } = setup();
    fireEvent.click(await screen.findByRole("link", { name: "View manuscript: Own source" }));
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    await act(() => router.navigate(-1));
    await waitFor(() => expect(screen.getByRole("link", { name: "View manuscript: Own source" })).toHaveFocus());
    await act(() => router.navigate(1));
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    await act(() => router.navigate(-1));
    await waitFor(() => expect(screen.getByRole("link", { name: "View manuscript: Own source" })).toHaveFocus());
  });

  it("does not let an abandoned draft overwrite a same-mounted history return", async () => {
    const { router } = setup("/library?q=first");
    const search = await screen.findByRole("searchbox", { name: "Search Library" });
    fireEvent.change(await screen.findByRole("combobox", { name: "Program" }), { target: { value: "IT" } });
    fireEvent.change(search, { target: { value: "abandoned" } });
    await act(() => router.navigate(-1));
    await act(() => new Promise((resolve) => window.setTimeout(resolve, 500)));
    expect(router.state.location.search).toBe("?q=first");
    expect(search).toHaveValue("first");
  });

  it.each([false, true])("restores a fresh/reloaded source's URL context (unavailable=%s)", async (unavailable) => {
    const { router } = setup(sourceRoute(true, [1, 2]), undefined, unavailable);
    if (unavailable) {
      await screen.findByRole("heading", { name: "This Library record is not available" });
      fireEvent.click(screen.getByRole("link", { name: "Return to Library" }));
    } else {
      await screen.findByRole("heading", { name: "Own source", level: 1 });
      fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    }
    const returned = await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(router.state.location.pathname + router.state.location.search).toBe(ORIGIN);
    expect(screen.getByRole("button", { name: "Exit comparison" })).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").every((box) => (box as HTMLInputElement).checked)).toBe(true);
    await waitFor(() => expect(returned).toHaveFocus());
  });

  it.each([
    "/library/1", "/library/1?library_return=%2F%2Fexample.com%2Flibrary",
    sourceRoute() + "&library_origin=1",
  ])("uses the safe Library fallback for absent/invalid context: %s", async (route) => {
    const { router } = setup(route);
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(router.state.location.pathname + router.state.location.search).toBe("/library");
    expect(screen.getByRole("heading", { name: "Manuscript Library", level: 1 })).toHaveFocus();
  });

  it("uses bounded heading fallback for a missing origin card", async () => {
    const { control } = setup(sourceRoute());
    control.filteredItems = [];
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    await screen.findByRole("heading", { name: "No records match your search and program" });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Manuscript Library", level: 1 })).toHaveFocus());
  });

  it.each(["fallback", "focus", "Tab", "pointer", "blur"])("late list data yields to %s", async (intent) => {
    const { control } = setup(sourceRoute());
    let release!: () => void;
    control.holdFiltered = new Promise<void>((resolve) => { release = resolve; });
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    const heading = await screen.findByRole("heading", { name: "Manuscript Library", level: 1 });
    const search = screen.getByRole("searchbox", { name: "Search Library" });
    if (intent === "fallback") {
      await waitFor(() => expect(heading).toHaveFocus());
    } else if (intent === "focus") {
      search.focus();
    } else if (intent === "Tab") {
      fireEvent.keyDown(document, { key: "Tab" }); search.focus();
    } else if (intent === "pointer") {
      fireEvent.pointerDown(search); search.focus();
    } else {
      fireEvent(window, new Event("blur")); search.focus();
    }
    const expected = intent === "fallback" ? heading : search;
    await act(async () => { release(); });
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(expected).toHaveFocus();
  });

  it.each(["/library/2", "/library/2?library_return=invalid"])("an unrelated source POP retires stale Library focus: %s", async (other) => {
    const valid = librarySourceDestination(1, { pathname: "/library", search: "" }, {
      comparisonMode: false, comparisonSelection: [],
    });
    const { router } = setup(valid, undefined, false, [other, "/library", valid]);
    await screen.findByRole("heading", { name: "Own source", level: 1 });
    await act(() => router.navigate(-2));
    await screen.findByRole("heading", { name: "Library record #2", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "Manuscript Library" }));
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(screen.getByRole("heading", { name: "Manuscript Library", level: 1 })).toHaveFocus();
  });
});
