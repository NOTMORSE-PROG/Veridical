import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryItemOut, PaginatedLibrary } from "../api/types";
import { SignalLibraryPage } from "./SignalLibrary";

const OWN: LibraryItemOut = {
  manuscript_id: 1, is_own: true, label: "Own source", title: "Own source",
  team_name: null, authors: [], program: "IT", school_year: null,
  original_filename: "own.pdf", status: "active", source: "library_submission",
  added_at: "2026-10-01T02:00:00Z", created_at: "2026-10-01T02:00:00Z",
  proposed_fields: [], possible_duplicate_of: null, failure_detail: null,
};
const ORIGIN = "/library?q=Pagination&program=IT&page=2&retained=value";
const KEY = ["library", "active", 2, "IT", "Pagination"];
const result = (items: LibraryItemOut[], total: number, page = 2): PaginatedLibrary =>
  ({ items, total, page, page_size: 50 });

function setup(initial = result([], 50), state?: Record<string, unknown>, route = ORIGIN) {
  const control: { data: PaginatedLibrary; hold?: Promise<void>; error?: boolean } = { data: initial };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    let data: unknown;
    if (url.pathname === "/programs") data = [{ id: 1, name: "IT" }];
    else if (url.pathname === "/library") {
      if (url.searchParams.get("scope") === "pending") data = result([], 0, 1);
      else if (url.searchParams.has("search") || url.searchParams.get("page") === "2") {
        await control.hold;
        if (control.error) return new Response('{"error":{"code":"api_down","message":"Fixture outage"}}', { status: 503 });
        data = { ...control.data, page: Number(url.searchParams.get("page") ?? 1) };
      } else data = result([OWN], 50, 1);
    } else if (url.pathname === "/library/1") data = OWN;
    else if (url.pathname === "/library/1/document") data = {
      manuscript_id: 1, original_filename: "own.pdf", source_format: "pdf",
      available: true, unavailable_reason: null, purged_at: null, page_count: 6, regions: [],
    };
    else return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter([{ path: "/library", element: <SignalLibraryPage /> }], {
    initialEntries: state ? [{ pathname: "/library", search: route.slice("/library".length), state }] : [route],
  });
  render(<StrictMode><QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider></StrictMode>);
  return { control, queryClient, router };
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Library page recovery (4z)", () => {
  it("distinguishes positive-total out-of-range from genuine no matches", async () => {
    const { router } = setup();
    expect(await screen.findByRole("heading", { name: "Page 2 is outside the current results" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /50 matching manuscripts/ })).toHaveTextContent("The current results end on page 1.");
    expect(screen.queryByRole("heading", { name: /No records match/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Library pages" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to page 1" })).toBeEnabled();
    expect(router.state.location.search).toBe(ORIGIN.slice("/library".length));
  });

  it.each([0, 1])("replaces only page and preserves full state with %i selected record", async (count) => {
    const state = { comparisonMode: true, comparisonSelection: count ? [1] : [], removedTitle: "Kept notice", unrelated: { safe: true } };
    const { router, control } = setup(result([], 50), state);
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    go.focus();
    control.data = result([OWN], 50, 1);
    fireEvent.click(go);
    expect(screen.getByRole("heading", { name: "Manuscript Library", level: 1 })).toHaveFocus();
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(router.state.location.search).toBe("?q=Pagination&program=IT&page=1&retained=value");
    expect(router.state.historyAction).toBe("REPLACE");
    expect(router.state.location.state).toEqual(state);
    expect(screen.getByRole("button", { name: "Exit comparison" })).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").filter((node) => (node as HTMLInputElement).checked)).toHaveLength(count);
  });

  it("does not let a slow recovered response reclaim Search focus", async () => {
    const { control } = setup();
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    let release!: () => void;
    control.hold = new Promise<void>((resolve) => { release = resolve; });
    control.data = result([OWN], 50, 1);
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    go.focus(); fireEvent.click(go);
    const search = screen.getByRole("searchbox");
    search.focus();
    await act(async () => { release(); });
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(search).toHaveFocus();
  });

  it("still navigates when its focus guard declines an unowned activation", async () => {
    const { router, control } = setup();
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    const search = screen.getByRole("searchbox");
    search.focus(); control.data = result([OWN], 50, 1);
    fireEvent.click(go);
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(router.state.location.search).toContain("page=1");
    expect(search).toHaveFocus();
  });

  it("shows no-match only for a genuine filtered zero", async () => {
    setup(result([], 0));
    expect(await screen.findByRole("heading", { name: "No records match your search and program" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Go to page/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Search covers your own records only.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear search and filter" })).toBeInTheDocument();
  });

  it("offers retry rather than no-match for defensive positive empty within range", async () => {
    setup(result([], 60));
    expect(await screen.findByText("Library records could not be shown on this page.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /No records match/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Library pages" })).not.toBeInTheDocument();
  });

  it("qualifies cached range while updating and retains focused Go", async () => {
    const { control, queryClient } = setup();
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    go.focus();
    let release!: () => void;
    control.hold = new Promise<void>((resolve) => { release = resolve; });
    await act(async () => { void queryClient.refetchQueries({ queryKey: KEY, exact: true }); });
    expect(await screen.findByText("Updating Library records.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page 2 is outside the current results" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Last loaded: 50 matching manuscripts/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to page 1" })).toBe(go);
    expect(go).toHaveFocus();
    control.data = result([OWN], 60);
    await act(async () => { release(); });
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(screen.getByRole("button", { name: "Go to page 1" })).toBe(go);
    expect(go).toHaveAttribute("aria-disabled", "true");
    screen.getByRole("searchbox").focus();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Go to page 1" })).not.toBeInTheDocument());
  });

  it("keeps cached zero out of completed no-match during retry then error", async () => {
    const { control, queryClient } = setup(result([], 0));
    await screen.findByRole("heading", { name: "No records match your search and program" });
    let release!: () => void;
    control.hold = new Promise<void>((resolve) => { release = resolve; });
    control.error = true;
    await act(async () => { void queryClient.refetchQueries({ queryKey: KEY, exact: true }); });
    await screen.findByText("Updating Library records.");
    expect(screen.queryByRole("heading", { name: /No records match/ })).not.toBeInTheDocument();
    await act(async () => { release(); });
    await screen.findByRole("alert");
    expect(screen.queryByRole("heading", { name: /No records match/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps focused Go through null-target blur and a background result change", async () => {
    const { queryClient } = setup();
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    go.focus();
    fireEvent.blur(go, { relatedTarget: null });
    await act(async () => { queryClient.setQueryData(KEY, result([OWN], 60)); });
    expect(screen.getByRole("button", { name: "Go to page 1" })).toBe(go);
    await waitFor(() => expect(go).toHaveAttribute("aria-disabled", "true"));
    screen.getByRole("searchbox").focus();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Go to page 1" })).not.toBeInTheDocument());
  });

  it("qualifies paused cached zero without claiming current no matches or busy fetching", async () => {
    const { queryClient } = setup(result([], 0));
    await screen.findByRole("heading", { name: "No records match your search and program" });
    await act(async () => { queryClient.getQueryCache().find({ queryKey: KEY, exact: true })!.setState({ fetchStatus: "paused" }); });
    expect(await screen.findByText("Waiting to update Library records.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /No records match/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Last loaded: 0 matching manuscripts/ })).toBeInTheDocument();
    expect(document.querySelector(".signal-library-results")).toHaveAttribute("aria-busy", "false");
  });

  it("keeps focused Retry mounted when cached defensive-empty data becomes nonempty", async () => {
    const { queryClient } = setup(result([], 60));
    const retry = await screen.findByRole("button", { name: "Try again" });
    retry.focus();
    await act(async () => { queryClient.setQueryData(KEY, result([OWN], 60)); });
    await screen.findByRole("link", { name: "View manuscript: Own source" });
    expect(screen.getByRole("button", { name: "Try again" })).toBe(retry);
    expect(retry).toHaveAttribute("aria-disabled", "true");
    screen.getByRole("searchbox").focus();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument());
  });

  it("makes a retained old-query Go a no-op until an in-page focus transfer", async () => {
    const { router } = setup();
    const go = await screen.findByRole("button", { name: "Go to page 1" });
    go.focus();
    await act(() => router.navigate("/library?q=New&program=IT&page=2"));
    await waitFor(() => expect(go).toHaveAttribute("aria-disabled", "true"));
    fireEvent.click(go);
    expect(router.state.location.search).toBe("?q=New&program=IT&page=2");
    expect(go).toHaveFocus();
    screen.getByRole("searchbox").focus();
    await waitFor(() => expect(screen.getByRole("button", { name: "Go to page 1" })).not.toHaveAttribute("aria-disabled"));
  });

  it.each(["go", "retry"])("retires a retained %s after actual blur to BODY then later Search focus", async (kind) => {
    const { queryClient } = setup(result([], kind === "go" ? 50 : 60));
    const action = await screen.findByRole("button", { name: kind === "go" ? "Go to page 1" : "Try again" });
    action.focus(); action.blur();
    expect(document.activeElement).toBe(document.body);
    await act(async () => { queryClient.setQueryData(KEY, result([OWN], 60)); });
    await waitFor(() => expect(action).toHaveAttribute("aria-disabled", "true"));
    screen.getByRole("searchbox").focus();
    await waitFor(() => expect(action).not.toBeInTheDocument());
  });
});

