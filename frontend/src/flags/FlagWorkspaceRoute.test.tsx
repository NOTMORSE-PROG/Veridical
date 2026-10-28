import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { FlagWorkspaceRoute } from "./FlagWorkspaceRoute";

vi.mock("./FlagDetail", () => ({ FlagDetailPage: () => <p>Current finding read and retry state</p> }));
afterEach(() => vi.unstubAllGlobals());
function start(reply: unknown, status = 200) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(reply), { status }));
  vi.stubGlobal("fetch", fetch);
  const state = { routeReturnFocus: { returnPath: "/report/5?review_step=findings", elementId: "signal-flag-review-7" } };
  function Target() { const current = useLocation(); return <output>{current.pathname + current.search}</output>; }
  const router = createMemoryRouter([{ path: "/flags/:flagId", element: <FlagWorkspaceRoute /> }, { path: "/report/:checkRunId/document", element: <Target /> }], { initialEntries: [{ pathname: "/flags/7", state }] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, state, fetch };
}
it("opens the real finding in the shared manuscript workspace and retains its return context", async () => {
  const control = start({ id: 7, check_run_id: 5, assessment_revision: 3, check_kind: "citation_integrity" });
  await waitFor(() => expect(control.router.state.location.pathname).toBe("/report/5/document"));
  const query = new URLSearchParams(control.router.state.location.search);
  expect(query.get("finding")).toBe("7"); expect(query.get("module")).toBe("citation_integrity"); expect(query.get("view")).toBe("finding");
  expect(control.router.state.location.state).toEqual(control.state);
  expect(control.router.state.historyAction).toBe("REPLACE");
  expect(control.fetch).toHaveBeenCalledTimes(1);
});
it.each([401, 403, 404, 409, 500])("keeps the guarded read state for HTTP %s without inventing a workspace destination", async status => {
  const control = start({ error: { code: "unavailable", message: "Read unavailable" } }, status);
  await screen.findByText("Current finding read and retry state");
  await waitFor(() => expect(control.fetch).toHaveBeenCalledTimes(1));
  expect(control.router.state.location.pathname).toBe("/flags/7");
});
it("does not redirect a mismatched finding response", async () => {
  const control = start({ id: 8, check_run_id: 5, assessment_revision: 3, check_kind: "citation_integrity" });
  await waitFor(() => expect(control.fetch).toHaveBeenCalledTimes(1));
  expect(control.router.state.location.pathname).toBe("/flags/7");
});
