import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode, useEffect, useRef, useState } from "react";
import {
  createMemoryRouter,
  Link,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouteAnnouncer } from "./RouteAnnouncer";
import { rememberRouteReturnFocus, useRouteFocus } from "./useRouteFocus";

function PersistentLayout() {
  return <><RouteAnnouncer /><Outlet /></>;
}

function EvidencePage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Evidence detail - VERIDICAL", headingRef);
  return (
    <main>
      <h1 ref={headingRef} tabIndex={-1}>Evidence detail</h1>
      <Link
        id="source-return-control"
        to="/document"
        onClick={() => rememberRouteReturnFocus("/flags/1", "/document", "source-return-control")}
      >
        View source
      </Link>
    </main>
  );
}

function DocumentPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Source manuscript - VERIDICAL", headingRef);
  return <main><h1 ref={headingRef} tabIndex={-1}>Source manuscript</h1></main>;
}

function AsyncReportPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [ready, setReady] = useState(false);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);
  useEffect(() => setReady(true), []);
  return (
    <main>
      <h1 ref={headingRef} tabIndex={-1}>Readiness report</h1>
      {ready && (
        <Link
          id="async-evidence-return-control"
          to="/flags/2"
          state={{ routeReturnFocus: { returnPath: "/report/2", elementId: "async-evidence-return-control" } }}
        >
          Review asynchronous evidence
        </Link>
      )}
    </main>
  );
}

function ImmediateReportPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);
  return (
    <main>
      <h1 ref={headingRef} tabIndex={-1}>Readiness report</h1>
      <Link
        id="immediate-evidence-return-control"
        to="/flags/3"
        state={{ routeReturnFocus: { returnPath: "/report/3", elementId: "immediate-evidence-return-control" } }}
      >
        Review cached evidence
      </Link>
    </main>
  );
}

function FlagFromRouteState() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  const returnFocus = (location.state as { routeReturnFocus?: { returnPath: string; elementId: string } } | null)?.routeReturnFocus;
  useRouteFocus("Evidence detail - VERIDICAL", headingRef, returnFocus);
  return <main><h1 ref={headingRef} tabIndex={-1}>Evidence detail</h1></main>;
}

describe("route focus and announcement", () => {
  afterEach(() => vi.restoreAllMocks());

  it("announces POP navigation and restores focus to the source trigger", async () => {
    const router = createMemoryRouter([
      {
        element: <PersistentLayout />,
        children: [
          { path: "/flags/1", element: <EvidencePage /> },
          { path: "/document", element: <DocumentPage /> },
        ],
      },
    ], { initialEntries: ["/flags/1"] });
    render(<RouterProvider router={router} />);

    fireEvent.click(screen.getByRole("link", { name: "View source" }));
    expect(await screen.findByRole("heading", { name: "Source manuscript" })).toHaveFocus();

    await act(async () => { await router.navigate(-1); });

    const returnControl = await screen.findByRole("link", { name: "View source" });
    await waitFor(() => expect(returnControl).toHaveFocus());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Page changed: Evidence detail."));
  });

  it("restores an asynchronously rendered report control registered in route state", async () => {
    const router = createMemoryRouter([{
      element: <PersistentLayout />,
      children: [
        { path: "/report/2", element: <AsyncReportPage /> },
        { path: "/flags/2", element: <FlagFromRouteState /> },
      ],
    }], { initialEntries: ["/report/2"] });
    render(<RouterProvider router={router} />);

    fireEvent.click(await screen.findByRole("link", { name: "Review asynchronous evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence detail" })).toHaveFocus();

    await act(async () => { await router.navigate(-1); });

    const returnControl = await screen.findByRole("link", { name: "Review asynchronous evidence" });
    await waitFor(() => expect(returnControl).toHaveFocus());
  });

  it("keeps immediate return focus through the StrictMode effect replay", async () => {
    const router = createMemoryRouter([{
      element: <PersistentLayout />,
      children: [
        { path: "/report/3", element: <ImmediateReportPage /> },
        { path: "/flags/3", element: <FlagFromRouteState /> },
      ],
    }], { initialEntries: ["/report/3"] });
    render(<StrictMode><RouterProvider router={router} /></StrictMode>);

    fireEvent.click(screen.getByRole("link", { name: "Review cached evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence detail" })).toHaveFocus();

    await act(async () => { await router.navigate(-1); });

    const returnControl = await screen.findByRole("link", { name: "Review cached evidence" });
    await waitFor(() => expect(returnControl).toHaveFocus());
  });
});
