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

// BUG-167: mirrors `FlagDetail.tsx`'s own breadcrumb -- a real in-app
// `<Link>` back to the EXACT `returnPath` an in-app return control was
// registered with, so a PUSH-type return can be exercised the same way a
// breadcrumb click is, not just `router.navigate(-1)` (a POP).
function FlagWithBreadcrumb() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  const returnFocus = (location.state as { routeReturnFocus?: { returnPath: string; elementId: string } } | null)?.routeReturnFocus;
  useRouteFocus("Evidence detail - VERIDICAL", headingRef, returnFocus);
  return (
    <main>
      <h1 ref={headingRef} tabIndex={-1}>Evidence detail</h1>
      {returnFocus && <Link to={returnFocus.returnPath}>VERIDICAL</Link>}
    </main>
  );
}

function ReportWithBreadcrumbTarget({ path }: { path: string }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);
  return (
    <main>
      <h1 ref={headingRef} tabIndex={-1}>Readiness report</h1>
      <Link
        id="breadcrumb-review-control"
        to="/flags/5"
        state={{ routeReturnFocus: { returnPath: path, elementId: "breadcrumb-review-control" } }}
      >
        Review evidence
      </Link>
    </main>
  );
}

// BUG-167: identical to `AsyncReportPage` except `ready` never flips --
// the registered return control genuinely never appears in the DOM,
// reproducing "the instructor's list view reset to a state that no
// longer shows the target flag," the exact case that used to leave focus
// abandoned on <body> forever instead of falling back to the heading.
function AsyncReportPageNeverReady() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Readiness report - VERIDICAL", headingRef);
  return <main><h1 ref={headingRef} tabIndex={-1}>Readiness report</h1></main>;
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

  it("BUG-167: restores return focus for an in-app link back to the exact origin, not just browser Back", async () => {
    const router = createMemoryRouter([{
      element: <PersistentLayout />,
      children: [
        { path: "/report/4", element: <ReportWithBreadcrumbTarget path="/report/4" /> },
        { path: "/flags/5", element: <FlagWithBreadcrumb /> },
      ],
    }], { initialEntries: ["/report/4"] });
    render(<RouterProvider router={router} />);

    fireEvent.click(screen.getByRole("link", { name: "Review evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence detail" })).toHaveFocus();

    // A real in-app Link click (PUSH), not `router.navigate(-1)` (POP) --
    // this is what a breadcrumb click actually does in the real app.
    fireEvent.click(screen.getByRole("link", { name: "VERIDICAL" }));

    const returnControl = await screen.findByRole("link", { name: "Review evidence" });
    await waitFor(() => expect(returnControl).toHaveFocus());
  });

  it("BUG-167: falls back to the heading, never leaves focus abandoned, when the registered return control never appears", async () => {
    const router = createMemoryRouter([{
      element: <PersistentLayout />,
      children: [
        { path: "/report/6", element: <AsyncReportPageNeverReady /> },
        { path: "/flags/6", element: <FlagFromRouteState /> },
      ],
    }], { initialEntries: ["/report/6"] });
    render(<RouterProvider router={router} />);

    // Register a return focus manually (the report page here never
    // renders a matching control at all -- reproducing "the list view
    // reset to a state where the target flag isn't shown").
    await act(async () => {
      await router.navigate("/flags/6", {
        state: {
          routeReturnFocus: { returnPath: "/report/6", elementId: "flag-that-never-reappears" },
        },
      });
    });
    expect(await screen.findByRole("heading", { name: "Evidence detail" })).toHaveFocus();

    await act(async () => { await router.navigate(-1); });

    const heading = await screen.findByRole("heading", { name: "Readiness report" });
    await waitFor(() => expect(heading).toHaveFocus(), { timeout: 2000 });
    expect(document.activeElement).not.toBe(document.body);
  });
});
