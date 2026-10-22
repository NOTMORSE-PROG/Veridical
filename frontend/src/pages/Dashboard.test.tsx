import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { reviewDeskPath } from "../dashboard/useReviewDesk";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DashboardPage } from "./Dashboard";

const ACTIVE_FAMILY = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 24,
    report_count: 1,
  },
];

const STATS = {
  manuscripts_checked: 3,
  ready_count: 1,
  conditionally_ready_count: 1,
  not_ready_count: 1,
  needs_review_count: 0,
  escalations_awaiting_review: 2,
  escalation_rate: 0.5,
  escalation_budget: 0.2,
  system_underperforming: true,
  decided_count: 1,
  // BUG-211: deliberately NOT what the old `manuscripts_checked -
  // needs_review_count - decided_count` formula would produce (3 - 0 - 1
  // = 2) -- if the badge ever regresses back to deriving this instead of
  // reading the real field, this fixture makes that regression visible.
  ready_to_decide_count: 1,
  // BUG-212: used to have no backing field at all when its tab wasn't
  // selected -- the "In progress" assertion below reads this directly.
  needs_attention_count: 5,
  checking_count: 3,
  not_checked_count: 4,
};

const MANUSCRIPTS_PAGE = {
  items: [
    {
      id: 1,
      group_label: "G-11",
      ingest_status: "done",
      ingest_failure_reason: null,
      created_at: "2026-01-01T00:00:00Z",
      latest_check_run_id: 7,
      latest_check_run_status: "done",
      latest_done_check_run_id: 7,
      latest_readiness: "conditionally_ready",
      latest_decision: null,
      latest_done_rubric_family_id: "fam-1",
      escalations_awaiting_review: 2,
      program: "BSIT",
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
};

function stubCompleteDesk(stats: unknown = { ...STATS, complete_queue_count: 27 }, list?: (url: URL) => unknown) {
  const fallback = stubFetchByPath({
    "/auth/me": { id: 1, email: "a@b.com", display_name: "Instructor" },
    "/rubric-families": ACTIVE_FAMILY, "/stats": stats, "/programs": [],
  });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input.toString(), "http://localhost");
    if (url.pathname !== "/manuscripts") return fallback(input, init);
    const body = list ? list(url) : MANUSCRIPTS_PAGE;
    return Promise.resolve(body instanceof Response ? body : new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("DashboardPage", () => {
  afterEach(() => { onlineManager.setOnline(true); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it.each([
    { start: 1, target: 2, button: "Next", disabled: "Next" },
    { start: 2, target: 1, button: "Previous", disabled: "Previous" },
  ])("BUG-436: $button hands focus over before a deferred page request and boundary change", async ({ start, target, button, disabled }) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let finish!: (response: Response) => void;
    let requestFocus: Element | null = null;
    const fallback = stubCompleteDesk(undefined, () => ({ ...MANUSCRIPTS_PAGE, total: 21, page: start }));
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), "http://localhost");
      if (url.pathname === "/manuscripts" && Number(url.searchParams.get("page")) === target) {
        requestFocus = document.activeElement;
        return new Promise<Response>((resolve) => { finish = resolve; });
      }
      return fallback(input, init);
    }));
    renderWithProviders(<DashboardPage />, { route: `/dashboard?queue=complete&page=${start}&program=BSIT&sort=oldest&q=applied` });
    const trigger = await screen.findByRole("button", { name: button });
    trigger.focus(); fireEvent.click(trigger);
    const heading = screen.getByRole("heading", { name: "Complete", level: 2 });
    expect(heading).toHaveFocus();
    await waitFor(() => expect(requestFocus).toBe(heading));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(`Loading page ${target} in Complete.`);
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).not.toHaveTextContent("21 matching manuscripts");
    const search = screen.getByRole("searchbox");
    search.focus();
    await act(async () => finish(new Response(JSON.stringify({ ...MANUSCRIPTS_PAGE, total: 21, page: target }), { status: 200 })));
    await waitFor(() => expect(status).toHaveTextContent(`Complete: Page ${target} of 2. 21 matching manuscripts.`));
    expect(status).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("button", { name: disabled })).toBeDisabled();
    expect(search).toHaveFocus();
    expect(fallback.mock.calls.some(([input]) => {
      const url = new URL(input.toString(), "http://localhost");
      return url.pathname === "/manuscripts" && url.searchParams.get("program") === "BSIT" && url.searchParams.get("sort") === "oldest" && url.searchParams.get("q") === "applied";
    })).toBe(true);
  });

  it.each(["outside", "inactive window"])("BUG-436: paging preserves %s focus intent", async (intent) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(intent !== "inactive window");
    stubCompleteDesk(undefined, (url) => ({ ...MANUSCRIPTS_PAGE, total: 21, page: Number(url.searchParams.get("page")) }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete" });
    const next = await screen.findByRole("button", { name: "Next" });
    const search = screen.getByRole("searchbox");
    (intent === "outside" ? search : next).focus();
    const focus = vi.spyOn(screen.getByRole("heading", { name: "Complete", level: 2 }), "focus");
    fireEvent.click(next);
    await screen.findByText("Page 2 of 2");
    expect(focus).not.toHaveBeenCalled();
    if (intent === "outside") expect(search).toHaveFocus();
  });

  it.each([true, false])("BUG-436: cached error retry (empty=%s) is loading then only error, preserving nonempty cards", async (empty) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let finish!: (response: Response) => void;
    let hold = false;
    const fallback = stubCompleteDesk(undefined, () => empty ? { ...MANUSCRIPTS_PAGE, total: 0, items: [] } : MANUSCRIPTS_PAGE);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (hold && new URL(input.toString(), "http://localhost").pathname === "/manuscripts") return new Promise<Response>((resolve) => { finish = resolve; });
      return fallback(input, init);
    }));
    function Refresh() {
      const client = useQueryClient();
      return <button onClick={() => { hold = true; void client.invalidateQueries({ queryKey: ["manuscripts"] }); }}>Refresh test queue</button>;
    }
    renderWithProviders(<><Refresh /><DashboardPage /></>, { route: "/dashboard?queue=complete" });
    if (empty) await screen.findByRole("heading", { name: "This queue is clear" });
    else await screen.findByText("G-11");
    const fail = () => new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled queue failure" } }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Refresh test queue" }));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    await act(async () => finish(fail()));
    await screen.findByRole("alert");
    expect(screen.queryByRole("heading", { name: "This queue is clear" })).not.toBeInTheDocument();
    if (!empty) expect(screen.getByText("G-11")).toBeVisible();
    const retry = screen.getByRole("button", { name: "Try again" });
    retry.focus(); fireEvent.click(retry);
    expect(screen.getByRole("heading", { name: "Complete", level: 2 })).toHaveFocus();
    const status = screen.getByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Loading page 1 in Complete."));
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "This queue is clear" })).not.toBeInTheDocument();
    if (!empty) expect(screen.getByText("G-11")).toBeVisible();
    await act(async () => finish(fail()));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load page 1 in Complete");
    expect(status).toBeEmptyDOMElement();
    expect(status).toHaveAttribute("aria-busy", "false");
    expect(screen.queryByRole("heading", { name: "This queue is clear" })).not.toBeInTheDocument();
  });

  it("BUG-436: a delayed router transition retains the target loading marker through the origin render", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let commitNavigation!: (value: null) => void;
    let finish!: (response: Response) => void;
    const fallback = stubCompleteDesk(undefined, () => ({ ...MANUSCRIPTS_PAGE, total: 21 }));
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), "http://localhost");
      if (url.pathname === "/manuscripts" && url.searchParams.get("page") === "2") return new Promise<Response>((resolve) => { finish = resolve; });
      return fallback(input, init);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([{
      path: "/dashboard", element: <DashboardPage />,
      loader: ({ request }) => new URL(request.url).searchParams.get("page") === "2"
        ? new Promise<null>((resolve) => { commitNavigation = resolve; }) : null,
    }], { initialEntries: ["/dashboard?queue=complete"] });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    const next = await screen.findByRole("button", { name: "Next" });
    next.focus(); fireEvent.click(next);
    await waitFor(() => expect(commitNavigation).toBeTypeOf("function"));
    expect(router.state.location.search).not.toContain("page=2");
    expect(screen.getByRole("heading", { name: "Complete", level: 2 })).toHaveFocus();
    await act(async () => commitNavigation(null));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    expect(screen.getByRole("status")).toHaveTextContent("Loading page 2 in Complete.");
    await act(async () => finish(new Response(JSON.stringify({ ...MANUSCRIPTS_PAGE, total: 21, page: 2 }), { status: 200 })));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Complete: Page 2 of 2. 21 manuscripts."));
  });

  it("BUG-436: paused cached retry stays busy until connectivity and request settlement", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let finish!: (response: Response) => void;
    let hold = false;
    const fallback = stubCompleteDesk(undefined, () => new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled failure" } }), { status: 503 }));
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (hold && new URL(input.toString(), "http://localhost").pathname === "/manuscripts") return new Promise<Response>((resolve) => { finish = resolve; });
      return fallback(input, init);
    }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete" });
    const retry = await screen.findByRole("button", { name: "Try again" });
    onlineManager.setOnline(false); hold = true;
    retry.focus(); fireEvent.click(retry);
    const status = screen.getByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Loading page 1 in Complete."));
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(finish).toBeUndefined();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => onlineManager.setOnline(true));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    expect(status).toHaveTextContent("Loading page 1 in Complete.");
    await act(async () => finish(new Response(JSON.stringify(MANUSCRIPTS_PAGE), { status: 200 })));
    await waitFor(() => expect(status).toHaveTextContent("Complete: Page 1 of 1. 1 manuscript."));
    expect(status).toHaveAttribute("aria-busy", "false");
  });

  it("BUG-436: Back after adopting a pending cached page abandons ownership before Forward", async () => {
    let finish!: (response: Response) => void;
    const fallback = stubCompleteDesk(undefined, () => ({ ...MANUSCRIPTS_PAGE, total: 21 }));
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), "http://localhost");
      if (url.pathname === "/manuscripts" && url.searchParams.get("page") === "2") return new Promise<Response>((resolve) => { finish = resolve; });
      return fallback(input, init);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const cachedPage = { ...MANUSCRIPTS_PAGE, total: 21, page: 2 };
    client.setQueryData(["manuscripts", "review-desk", { queue: "complete", sort: "newest", page: 2, pageSize: 20 }], cachedPage);
    const router = createMemoryRouter([{ path: "/dashboard", element: <DashboardPage /> }], { initialEntries: ["/dashboard?queue=complete"] });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true"));
    expect(screen.getByRole("status")).toHaveTextContent("Loading page 2 in Complete.");
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Complete: Page 1 of 2. 21 manuscripts."));
    await act(async () => { await router.navigate(1); });
    expect(screen.getByRole("status")).toHaveTextContent("Complete: Page 2 of 2. 21 manuscripts.");
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
    await act(async () => finish(new Response(JSON.stringify(cachedPage), { status: 200 })));
  });

  it("BUG-436: an old retry completion cannot clear a newer full-query action", async () => {
    const requests: Array<{ url: URL; finish: (response: Response) => void }> = [];
    let hold = false;
    const fail = () => new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled failure" } }), { status: 503 });
    const fallback = stubCompleteDesk(undefined, fail);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), "http://localhost");
      if (hold && url.pathname === "/manuscripts") return new Promise<Response>((finish) => { requests.push({ url, finish }); });
      return fallback(input, init);
    }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete&q=old&program=BSIT&sort=oldest" });
    const retry = await screen.findByRole("button", { name: "Try again" });
    hold = true; fireEvent.click(retry);
    await waitFor(() => expect(requests).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[0].url.searchParams.get("q")).toBe("old");
    expect(requests[1].url.searchParams.has("q")).toBe(false);
    expect(requests[1].url.searchParams.has("program")).toBe(false);
    expect(requests[1].url.searchParams.get("sort")).toBe("oldest");
    await act(async () => requests[1].finish(fail()));
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(requests).toHaveLength(3));
    await act(async () => requests[0].finish(new Response(JSON.stringify({ ...MANUSCRIPTS_PAGE, total: 9 }), { status: 200 })));
    expect(screen.getByRole("status")).toHaveTextContent("Loading page 1 in Complete.");
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    await act(async () => requests[2].finish(new Response(JSON.stringify(MANUSCRIPTS_PAGE), { status: 200 })));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Complete: Page 1 of 1. 1 manuscript."));
  });

  it("BUG-436: an unmarked cached refresh keeps cards, count and external focus without a page-loading announcement", async () => {
    let finish!: (response: Response) => void;
    let hold = false;
    const fallback = stubCompleteDesk();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (hold && new URL(input.toString(), "http://localhost").pathname === "/manuscripts") return new Promise<Response>((resolve) => { finish = resolve; });
      return fallback(input, init);
    }));
    function Refresh() {
      const client = useQueryClient();
      return <button onClick={() => { hold = true; void client.invalidateQueries({ queryKey: ["manuscripts"] }); }}>Refresh test queue</button>;
    }
    renderWithProviders(<><Refresh /><DashboardPage /></>, { route: "/dashboard?queue=complete" });
    await screen.findByText("G-11");
    const refresh = screen.getByRole("button", { name: "Refresh test queue" });
    refresh.focus(); fireEvent.click(refresh);
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    expect(screen.getByRole("status")).toHaveTextContent("Complete: Page 1 of 1. 1 manuscript.");
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText("G-11")).toBeVisible();
    expect(refresh).toHaveFocus();
    await act(async () => finish(new Response(JSON.stringify(MANUSCRIPTS_PAGE), { status: 200 })));
    expect(refresh).toHaveFocus();
  });

  it("BUG-430: Complete uses the current queue count, list total across pages, then its inactive count again", async () => {
    stubCompleteDesk(undefined, (url) => url.searchParams.get("status") === "decided"
      ? { ...MANUSCRIPTS_PAGE, page: Number(url.searchParams.get("page")), total: 27, items: [{ ...MANUSCRIPTS_PAGE.items[0], latest_decision: "approved" }] }
      : MANUSCRIPTS_PAGE);
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=checking" });
    const complete = await screen.findByRole("button", { name: /Complete27 manuscripts/ });
    expect(complete).toHaveTextContent("Latest check finished. Instructor decision recorded.");
    fireEvent.click(complete);
    expect(await screen.findByText("Page 1 of 2")).toBeVisible();
    expect(document.querySelector(".signal-desk__heading p[aria-live]")).toHaveTextContent("Complete: Page 1 of 2. 27 manuscripts.");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 2")).toBeVisible();
    expect(screen.getByRole("button", { name: /Complete27 manuscripts/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /In progress/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Complete27 manuscripts/ })).toHaveAttribute("aria-pressed", "false"));
  });

  it.each([0, 1])("BUG-430: selected Complete reports filtered total %s with atomic queue context", async (count) => {
    stubCompleteDesk(undefined, () => ({ ...MANUSCRIPTS_PAGE, total: count, items: count ? MANUSCRIPTS_PAGE.items : [] }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete&q=applied" });
    const expected = `${count} matching manuscript${count === 1 ? "" : "s"}`;
    expect(await screen.findByRole("button", { name: new RegExp(`Complete${expected}`) })).toHaveAttribute("aria-pressed", "true");
    const notice = document.querySelector(".signal-desk__heading p[aria-live]");
    expect(notice).toHaveTextContent(`Complete: Page 1 of 1. ${expected}.`);
    expect(notice).toHaveAttribute("aria-atomic", "true");
  });

  it("BUG-430: program Not set is applied filtering; an unsubmitted draft is not", async () => {
    stubCompleteDesk(undefined, () => ({ ...MANUSCRIPTS_PAGE, total: 1 }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete&program=__unset__" });
    await screen.findByRole("button", { name: /Complete1 matching manuscript/ });
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await screen.findByRole("button", { name: /Complete1 manuscript/ });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "unsubmitted draft" } });
    expect(screen.getByRole("button", { name: /Complete1 manuscript/ })).toBeInTheDocument();
    expect(document.querySelector(".signal-desk__heading p[aria-live]")).toHaveTextContent("Complete: Page 1 of 1. 1 manuscript.");
  });

  it("BUG-430: changing queue with an equal count changes the announcement context without moving focus", async () => {
    stubCompleteDesk({ ...STATS, complete_queue_count: 1 });
    renderWithProviders(<DashboardPage />);
    await screen.findByRole("button", { name: /Complete1 manuscript/ });
    expect(document.querySelector(".signal-desk__heading p[aria-live]")).toHaveTextContent("Needs you: Page 1 of 1. 1 manuscript.");
    const complete = screen.getByRole("button", { name: /Complete1 manuscript/ });
    complete.focus(); fireEvent.click(complete);
    await waitFor(() => expect(document.querySelector(".signal-desk__heading p[aria-live]")).toHaveTextContent("Complete: Page 1 of 1. 1 manuscript."));
    expect(complete).toHaveFocus();
  });

  it.each(["missing", "failed"])("BUG-430: omits an inactive Complete number when stats are %s", async (state) => {
    stubCompleteDesk(state === "missing" ? STATS : new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled stats failure" } }), { status: 503 }));
    renderWithProviders(<DashboardPage />);
    const complete = await screen.findByRole("button", { name: /Complete/ });
    await screen.findByRole("heading", { name: "Needs you" });
    expect(complete).not.toHaveTextContent(/\d|manuscripts/);
  });

  it.each(["pending", "error"])("BUG-430: selected Complete never falls back to global stats during list %s", async (state) => {
    const fetchMock = stubCompleteDesk(undefined, () => new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled list failure" } }), { status: 503 }));
    if (state === "pending") {
      const fetchOriginal = fetchMock.getMockImplementation()!;
      fetchMock.mockImplementation((input, init) => new URL(input.toString(), "http://localhost").pathname === "/manuscripts" ? new Promise<Response>(() => {}) : fetchOriginal(input, init));
    }
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=complete" });
    const complete = await screen.findByRole("button", { name: /Complete/ });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(input.toString(), "http://localhost").pathname === "/stats")).toBe(true));
    expect(complete).not.toHaveTextContent(/\d|manuscripts/);
  });

  it("BUG-430: a cached stats failure suppresses the inactive number", async () => {
    let failing = false;
    const fallback = stubCompleteDesk();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (failing && new URL(input.toString(), "http://localhost").pathname === "/stats") return Promise.resolve(new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled cached stats failure" } }), { status: 503 }));
      return fallback(input, init);
    }));
    function Refresh() {
      const client = useQueryClient();
      return <button onClick={() => { failing = true; void client.invalidateQueries({ queryKey: ["stats"] }); }}>Refresh test stats</button>;
    }
    renderWithProviders(<><Refresh /><DashboardPage /></>);
    await screen.findByRole("button", { name: /Complete27 manuscripts/ });
    fireEvent.click(screen.getByRole("button", { name: "Refresh test stats" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Complete/ })).not.toHaveTextContent(/\d|manuscripts/));
  });

  it("BUG-194: routes Needs you through the server's complete attention queue", () => {
    const path = reviewDeskPath({
      queue: "needs_review",
      sort: "needs_review_desc",
      page: 1,
      pageSize: 20,
    });
    const params = new URL(path, "https://veridical.test").searchParams;

    expect(params.get("status")).toBe("needs_attention");
    expect(params.has("needs_review")).toBe(false);
  });

  it("BUG-194: renders a failed upload's recovery actions inside Needs you", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              id: 71,
              group_label: "Upload recovery group",
              original_filename: "unreadable.pdf",
              ingest_status: "failed",
              ingest_failure_reason: "extraction_failed",
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: null,
              latest_check_run_status: null,
              latest_done_check_run_id: null,
              latest_readiness: null,
              latest_decision: null,
              latest_done_rubric_family_id: null,
              escalations_awaiting_review: 0,
              program: "BSIT",
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findAllByText("Upload recovery group");
    expect(screen.getByText("Upload needs attention")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try a corrected file" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("More actions"));
    expect(screen.getByRole("button", { name: "Remove from Review Desk" })).toBeInTheDocument();
  });

  // BUG-066: `file_too_large` can come from three different underlying
  // caps (raw upload MB, PDF page count, DOCX decompressed MB) -- a single
  // blanket "over the MB limit" sentence would misstate two of the three.
  // The row must show the SPECIFIC message the failing check computed
  // (`ingest_failure_detail`), never a guessed generic one, whenever it's
  // present.
  it("BUG-066: a failed row shows the specific computed reason, not a generic one", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              id: 72,
              group_label: "Oversized PDF group",
              original_filename: "huge.pdf",
              ingest_status: "failed",
              ingest_failure_reason: "file_too_large",
              ingest_failure_detail: "This PDF has 900 pages, more than the 500 page limit. Please split it or upload a shorter version.",
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: null,
              latest_check_run_status: null,
              latest_done_check_run_id: null,
              latest_readiness: null,
              latest_decision: null,
              latest_done_rubric_family_id: null,
              escalations_awaiting_review: 0,
              program: "BSIT",
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findAllByText("Oversized PDF group");
    expect(
      screen.getByText("This PDF has 900 pages, more than the 500 page limit. Please split it or upload a shorter version."),
    ).toBeInTheDocument();
    // The generic per-bucket sentence (which would be wrong here -- this
    // failure has nothing to do with the raw upload MB cap) must not render.
    expect(screen.queryByText("The file was larger than VERIDICAL currently accepts.")).not.toBeInTheDocument();
  });

  // Older rows (ingested before `ingest_failure_detail` existed) and the
  // catch-all `extraction_failed` reason never got a safe specific message
  // -- the row must fall back honestly to the generic sentence, not blank
  // or fabricate one.
  it("BUG-066: falls back to the generic reason when no specific detail was recorded", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              id: 73,
              group_label: "Legacy failed group",
              original_filename: "old.pdf",
              ingest_status: "failed",
              ingest_failure_reason: "file_too_large",
              ingest_failure_detail: null,
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: null,
              latest_check_run_status: null,
              latest_done_check_run_id: null,
              latest_readiness: null,
              latest_decision: null,
              latest_done_rubric_family_id: null,
              escalations_awaiting_review: 0,
              program: "BSIT",
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findAllByText("Legacy failed group");
    expect(screen.getByText("The file was larger than VERIDICAL currently accepts.")).toBeInTheDocument();
  });

  it("renders the first-run empty state (screen 4b) with the 3-step guide", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
      }),
    );
    renderWithProviders(<DashboardPage />);
    // Staged reveal: nothing paints until `families` resolves (avoids a
    // flash of the wrong screen), so this is asynchronous now.
    expect(await screen.findByText("No required format yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload required format" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a check" })).toBeEnabled();
    expect(screen.getByRole("list", { name: "First check setup" })).toBeInTheDocument();
  });

  it("BUG-023: 'New check' with no active rubric explains what's missing instead of dead-clicking silently", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
        "/manuscripts": { items: [], total: 0, page: 1, page_size: 200 },
      }),
    );
    renderWithProviders(<DashboardPage />);
    const newCheck = await screen.findByRole("button", { name: "Start a check" });
    expect(newCheck).toBeEnabled();
    fireEvent.click(newCheck);
    expect(
      await screen.findByText("No active rubric is available yet."),
    ).toBeInTheDocument();
  });

  it("renders the populated dashboard (screen 4e) once a rubric is active", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": MANUSCRIPTS_PAGE,
      }),
    );
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByRole("navigation", { name: "Review Desk queues" })).toBeInTheDocument();
    expect(screen.queryByText("No required format yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a check" })).toBeEnabled();
    expect((await screen.findAllByText("G-11")).length).toBeGreaterThan(0);
    expect(screen.getByText("Conditionally Ready")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review 2 unresolved criteria. Opens the manuscript and analysis." })).toHaveAttribute(
      "href",
      "/report/7/document?view=decision&pane=review",
    );
    expect(screen.getByRole("button", { name: /Needs you1 manuscript/ })).toBeInTheDocument();
    expect(screen.getByText("2").closest(".signal-record__assessment")).toHaveTextContent(
      "2 criterion tasks need you",
    );
    // BUG-211: reads STATS.ready_to_decide_count (1) directly -- the old
    // `manuscripts_checked - needs_review_count - decided_count` formula
    // would have shown "2 manuscripts" here instead (3 - 0 - 1).
    expect(
      screen.getByRole("button", { name: /Ready to decide1 manuscript/ }),
    ).toBeInTheDocument();
    // BUG-212: "In progress" isn't the active tab here, and used to show
    // no count at all -- now reads STATS.checking_count directly.
    expect(screen.getByRole("button", { name: /In progress3 manuscripts/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Not checked4 manuscripts/ })).toBeInTheDocument();
  });

  it("V-081: exposes the unchecked queue with an account-wide count and explicitly scoped search", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
      "/rubric-families": ACTIVE_FAMILY,
      "/stats": STATS,
      "/manuscripts": { items: [], total: 0, page: 1, page_size: 20 },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<DashboardPage />, { route: "/dashboard?q=missing" });

    fireEvent.click(await screen.findByRole("button", { name: /Not checked4 manuscripts/ }));
    expect(await screen.findByRole("heading", { name: "No matches in Not checked" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Not checked4 manuscripts/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Search group or file in this queue")).toHaveValue("missing");
    expect(screen.getByText("Only manuscripts in Not checked are searched. Change queues to find other uploads.")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = new URL(input.toString(), "http://localhost");
      return url.pathname === "/manuscripts" && url.searchParams.get("status") === "not_checked"
        && url.searchParams.get("q") === "missing";
    })).toBe(true));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("V-081: distinguishes an empty unchecked queue from a completed evaluation", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
      "/rubric-families": ACTIVE_FAMILY,
      "/stats": { ...STATS, not_checked_count: 0 },
      "/manuscripts": { items: [], total: 0, page: 1, page_size: 20 },
    }));
    renderWithProviders(<DashboardPage />, { route: "/dashboard?queue=not_checked" });
    expect(await screen.findByRole("heading", { name: "No unchecked manuscripts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Not checked0 manuscripts/ })).toBeInTheDocument();
    expect(screen.queryByText("Check complete")).not.toBeInTheDocument();
  });

  it("BUG-190: completed check evidence wins over a stale pending ingest state", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              ...MANUSCRIPTS_PAGE.items[0],
              id: 47,
              group_label: "V069 Demo Group",
              ingest_status: "pending",
              latest_check_run_id: 52,
              latest_check_run_status: "done",
              latest_done_check_run_id: 52,
              escalations_awaiting_review: 0,
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findAllByText("V069 Demo Group");
    expect(screen.getByText("Check complete")).toBeInTheDocument();
    expect(screen.queryByText("Preparing manuscript")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review manuscript and decide. Opens the manuscript and analysis." })).toHaveAttribute(
      "href",
      "/report/52/document",
    );
  });

  it("BUG-190: a pending manuscript with no check run still reports preparation and no decision action", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              ...MANUSCRIPTS_PAGE.items[0],
              id: 48,
              group_label: "Preparing Group",
              ingest_status: "pending",
              latest_check_run_id: null,
              latest_check_run_status: null,
              latest_done_check_run_id: null,
              latest_readiness: null,
              escalations_awaiting_review: 0,
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );

    renderWithProviders(<DashboardPage />);

    await screen.findAllByText("Preparing Group");
    expect(screen.getByText("Preparing manuscript")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Review manuscript and decide/ })).not.toBeInTheDocument();
    expect(screen.getByText("No action yet. The file is still being prepared.")).toBeInTheDocument();
  });

  it("BUG-137: a manuscript whose very FIRST check run failed offers 'Start a fresh check' in More actions, not a dead end", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": {
          items: [
            {
              id: 9,
              group_label: "G-19",
              ingest_status: "done",
              ingest_failure_reason: null,
              created_at: "2026-01-01T00:00:00Z",
              latest_check_run_id: 30,
              latest_check_run_status: "failed",
              latest_done_check_run_id: null,
              latest_readiness: null,
              latest_decision: null,
              latest_done_rubric_family_id: null,
              escalations_awaiting_review: 0,
              program: "BSIT",
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
    );
    renderWithProviders(<DashboardPage />);
    await screen.findAllByText("G-19");
    const moreActions = screen.getAllByText("More actions")[0];
    fireEvent.click(moreActions);
    expect(screen.queryByRole("button", { name: "Run again" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a fresh check" })).toBeInTheDocument();
  });

  it("V-059: Upload manuscript is available even with no active rubric yet (BUG-023 precedent: destination explains, never hidden)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": {
          id: 1,
          email: "a@b.com",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
        "/rubric-families": [],
      }),
    );
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByRole("button", { name: "Add manuscript" })).toBeEnabled();
  });

  // BUG-128: a report's integrity-check coverage gap used to name a
  // problem with no path to close it. The remedy now deep-links here with
  // `?rerun=<manuscript_id>` and opens the SAME modal the row-level "Run
  // again" button already opens, reachable one click earlier.
  it("BUG-128: ?rerun=<manuscript_id> opens the rerun modal preselected to that manuscript", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": MANUSCRIPTS_PAGE,
      }),
    );

    renderWithProviders(<DashboardPage />, { route: "/dashboard?rerun=1" });

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("G-11")).toBeInTheDocument();
  });

  it.each(["start", "close"])("V-059/V-081: uploaded manuscript handoff via %s", async (nextAction) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input.toString(), "http://localhost").pathname;
      if (path === "/auth/me") {
        return new Response(
          JSON.stringify({ id: 1, email: "a@b.com", display_name: "Demo Instructor" }),
          { status: 200 },
        );
      }
      if (path === "/rubric-families") return new Response(JSON.stringify(ACTIVE_FAMILY), { status: 200 });
      if (path === "/stats") return new Response(JSON.stringify(STATS), { status: 200 });
      if (path === "/manuscripts/ingest" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            manuscript_id: 99,
            group_label: "Ungrouped",
            ingest_status: "done",
            page_count: 8,
            anchor_kind: "page",
            image_only: false,
            text_chars: 100,
            images: 0,
            tables: 0,
            equations: 0,
            citations: 3,
            vision_status: "none",
            notes: [],
            group_proposal: {
              title: null,
              short_name: null,
              members: [],
              program: null,
              adviser: null,
              extraction_failed: false,
            },
          }),
          { status: 200 },
        );
      }
      if (path === "/manuscripts") {
        // Reflects the newly-ingested manuscript once the picker refetches
        // (real invalidation behavior, not a hand-wired test double).
        return new Response(
          JSON.stringify({
            items: [
              {
                id: 99,
                group_label: "Ungrouped",
                original_filename: "thesis.pdf",
                ingest_status: "done",
                ingest_failure_reason: null,
                created_at: "2026-01-01T00:00:00Z",
                latest_check_run_id: null,
                latest_check_run_status: null,
                latest_done_check_run_id: null,
              },
            ],
            total: 1,
            page: 1,
            page_size: 200,
          }),
          { status: 200 },
        );
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<DashboardPage />, { route: "/dashboard?q=previous-search" });

    fireEvent.click(await screen.findByRole("button", { name: "Add manuscript" }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["dummy"], "thesis.pdf", { type: "application/pdf" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    // Scoped to the dialog: the header's own "Upload manuscript" CTA is
    // still in the DOM behind the modal and shares the same accessible
    // name as the modal's submit button.
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload manuscript" }));

    await screen.findByText("8 pages parsed. 3 reference records extracted.");
    if (nextAction === "close") {
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.getByRole("button", { name: /Not checked4 manuscripts/ })).toHaveAttribute("aria-pressed", "true"));
      expect(screen.getByLabelText("Search group or file in this queue")).toHaveValue("");
      await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
        const url = new URL(input.toString(), "http://localhost");
        return url.pathname === "/manuscripts" && url.searchParams.get("status") === "not_checked"
          && !url.searchParams.has("q");
      })).toBe(true));
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([input]) =>
        new URL(input.toString(), "http://localhost").pathname)).toEqual(["/manuscripts/ingest"]);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      return;
    }
    fireEvent.click(screen.getByRole("button", { name: "Start a check with this manuscript" }));

    // Lands in New Check with the just-uploaded manuscript preselected,
    // no manual re-pick required (Flow B as one continuous task).
    const select = await screen.findByRole("combobox", { name: /manuscript/i });
    await waitFor(() => expect(select).toHaveValue("99"));
  });
});
