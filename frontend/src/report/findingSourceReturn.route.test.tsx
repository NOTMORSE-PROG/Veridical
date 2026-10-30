import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { createMemoryRouter, Link, RouterProvider, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { useRouteFocus } from "../routing/useRouteFocus";
import { findingSourceReturn } from "./findingSourceReturn";

function Report() {
  const heading = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  useRouteFocus("Report", heading);
  return <main><h1 ref={heading} tabIndex={-1}>Readiness report</h1>
    <Link id="signal-flag-review-7" to="/report/5/document?view=finding&finding=7"
      state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: "signal-flag-review-7" } }}>Review evidence</Link></main>;
}

function Viewer() {
  const heading = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  useRouteFocus("Manuscript review", heading, findingSourceReturn(location.state, 5));
  return <main><h1 ref={heading} tabIndex={-1}>Manuscript review</h1></main>;
}

describe("coming back from a finding's evidence", () => {
  it("puts focus back on the finding that was opened, not the top of the report", async () => {
    const router = createMemoryRouter([
      { path: "/report/:id", element: <Report /> },
      { path: "/report/:id/document", element: <Viewer /> },
    ], { initialEntries: ["/report/5?section=findings"] });
    render(<RouterProvider router={router} />);
    fireEvent.click(screen.getByRole("link", { name: "Review evidence" }));
    await screen.findByRole("heading", { name: "Manuscript review" });
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(screen.getByRole("link", { name: "Review evidence" })).toHaveFocus());
  });
});
