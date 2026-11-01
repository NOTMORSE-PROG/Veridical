import { act, render, screen } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { QUIET_ROUTE_CHANGE, RouteAnnouncer } from "./RouteAnnouncer";

afterEach(() => vi.unstubAllGlobals());

it("announces a route change but stays quiet for a panel swap that says what changed itself", async () => {
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 0; });
  document.title = "Readiness report (shared) - VERIDICAL";
  const router = createMemoryRouter([{ element: <><RouteAnnouncer /><Outlet /></>, children: [{ path: "/shared/:token", element: <p>Report</p> }] }],
    { initialEntries: ["/shared/tok?module=internal_agreement"] });
  render(<RouterProvider router={router} />);
  const region = screen.getByRole("status");
  await act(async () => { await router.navigate("/shared/tok?module=citation_integrity", { state: QUIET_ROUTE_CHANGE }); });
  expect(region).toHaveTextContent("");
  await act(async () => { await router.navigate("/shared/tok?section=grades"); });
  expect(region).toHaveTextContent("Page changed: Readiness report (shared).");
});
