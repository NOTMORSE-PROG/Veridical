import { StrictMode, useRef, useState } from "react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActionLink } from "../ui/ActionLink";
import { ReviewDeskReturnLink } from "./ReviewDeskReturnLink";
import { WorkflowLink } from "./WorkflowLink";
import { useRouteFocus, type RouteReturnFocusRegistration } from "./useRouteFocus";

const origin = "/dashboard?queue=ready_to_decide&status=checked&needs_review=false&q=Serbigo&program=IT&sort=oldest&page=3";

function setup(initial = origin, remainingRecord = false) {
  let omitRecord = false;
  let refreshRecord: () => void = () => {};
  let revealRecord: () => void = () => {};
  let replaceRecord: () => void = () => {};
  function Desk() {
    const headingRef = useRef<HTMLHeadingElement>(null);
    const [showRecord, setShowRecord] = useState(!omitRecord);
    const [recordVersion, setRecordVersion] = useState(0);
    refreshRecord = () => setShowRecord(false);
    revealRecord = () => setShowRecord(true);
    replaceRecord = () => setRecordVersion((version) => version + 1);
    useRouteFocus("Review Desk", headingRef);
    return <main><h1 ref={headingRef} tabIndex={-1}>Review Desk</h1><button>Queue filter</button>{showRecord && <>
      <ActionLink key={recordVersion} id="review-desk-primary-16" to="/report/20/document">Open manuscript</ActionLink>
      <details><summary>More actions</summary><WorkflowLink id="review-desk-prior-16" to="/report/18">Prior report</WorkflowLink></details>
    </>}{remainingRecord && <ActionLink id="review-desk-primary-17" to="/checks/21">View remaining check</ActionLink>}</main>;
  }
  function Review() {
    const location = useLocation();
    const headingRef = useRef<HTMLHeadingElement>(null);
    const returnFocus = (location.state as { routeReturnFocus?: RouteReturnFocusRegistration } | null)?.routeReturnFocus;
    useRouteFocus("Review", headingRef, returnFocus);
    return <main><h1 ref={headingRef} tabIndex={-1}>Review</h1>
      <ReviewDeskReturnLink>Return to Review Desk</ReviewDeskReturnLink>
      <ActionLink to="/report/20?flags_view=high">Full report</ActionLink>
      <ActionLink to="/report/20/document?finding=38&anchor=paragraph%204">Source location</ActionLink>
      <ActionLink id="criterion-evidence-control" to="/flags/38" state={{ routeReturnFocus: { returnPath: location.pathname + location.search, elementId: "criterion-evidence-control" }, callerValue: "retained" }}>Evidence</ActionLink>
      {returnFocus && <WorkflowLink to={returnFocus.returnPath}>Return to report</WorkflowLink>}
    </main>;
  }
  const router = createMemoryRouter([
    { path: "/dashboard", element: <Desk /> },
    { path: "/report/:id/*", element: <Review key="report" /> },
    { path: "/flags/:id", element: <Review key="flag" /> },
    { path: "/checks/:id", element: <Review /> },
  ], { initialEntries: [initial] });
  render(<StrictMode><RouterProvider router={router} /></StrictMode>);
  return {
    router,
    removeRecord: () => { omitRecord = true; },
    refreshRecord: () => refreshRecord(),
    revealRecord: () => revealRecord(),
    replaceRecord: () => replaceRecord(),
  };
}

describe("Review Desk round trips", () => {
  it.each([false, true])("recovers when a restored cached row disappears after a slow refresh (remaining=%s)", async (remainingRecord) => {
    const { router, refreshRecord } = setup(origin, remainingRecord);
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open manuscript" })).toHaveFocus());
    // Refresh can finish after the separate missing-target timeout.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 650)); });
    act(() => refreshRecord());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Review Desk" })).toHaveFocus());
    expect(router.state.location.pathname + router.state.location.search).toBe(origin);
    expect(screen.queryByRole("link", { name: "Open manuscript" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View remaining check" }) !== null).toBe(remainingRecord);
  });

  it.each([false, true])("recovers when removal blurs the origin before detaching it (remaining=%s)", async (remainingRecord) => {
    const { refreshRecord } = setup(origin, remainingRecord);
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    const cached = await screen.findByRole("link", { name: "Open manuscript" });
    await waitFor(() => expect(cached).toHaveFocus());
    // React can blur a focused node while it is still connected, just
    // before replacing cached rows. There has been no user focus intent.
    act(() => cached.blur());
    expect(document.activeElement).toBe(document.body);
    act(() => refreshRecord());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Review Desk" })).toHaveFocus());
  });

  it("keeps an instructor's deliberate focus movement when the cached origin later disappears", async () => {
    const { refreshRecord } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open manuscript" })).toHaveFocus());
    const filter = screen.getByRole("button", { name: "Queue filter" });
    act(() => filter.focus());
    act(() => refreshRecord());
    await waitFor(() => expect(screen.queryByRole("link", { name: "Open manuscript" })).not.toBeInTheDocument());
    expect(filter).toHaveFocus();
  });

  it.each(["tab", "shift-tab", "pointer", "window blur"])("does not recover focus after the instructor gives %s intent", async (intent) => {
    const { refreshRecord } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    const originAction = await screen.findByRole("link", { name: "Open manuscript" });
    await waitFor(() => expect(originAction).toHaveFocus());
    if (intent === "tab" || intent === "shift-tab") fireEvent.keyDown(originAction, { key: "Tab", shiftKey: intent === "shift-tab" });
    else if (intent === "pointer") fireEvent.pointerDown(screen.getByRole("button", { name: "Queue filter" }));
    else fireEvent.blur(window);
    act(() => refreshRecord());
    await waitFor(() => expect(originAction).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Review Desk" })).not.toHaveFocus();
  });

  it("restores a replacement DOM node with the same logical origin", async () => {
    const { replaceRecord } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    const cached = await screen.findByRole("link", { name: "Open manuscript" });
    await waitFor(() => expect(cached).toHaveFocus());
    act(() => replaceRecord());
    await waitFor(() => expect(screen.getByRole("link", { name: "Open manuscript" })).toHaveFocus());
    expect(screen.getByRole("link", { name: "Open manuscript" })).not.toBe(cached);
  });

  it.each([false, true])("does not steal chosen focus while an initially missing origin is pending (appears=%s)", async (appears) => {
    const { removeRecord, revealRecord } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    removeRecord();
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    const filter = await screen.findByRole("button", { name: "Queue filter" });
    act(() => filter.focus());
    if (appears) act(() => revealRecord());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 650)); });
    expect(filter).toHaveFocus();
  });

  it("does not apply an old restoration after navigation unmounts its route", async () => {
    const { router } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    const originAction = await screen.findByRole("link", { name: "Open manuscript" });
    await waitFor(() => expect(originAction).toHaveFocus());
    await act(async () => { await router.navigate("/checks/22"); });
    act(() => originAction.remove());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Review" })).toHaveFocus());
    expect(screen.queryByRole("heading", { name: "Review Desk" })).not.toBeInTheDocument();
  });

  it("preserves nested report filter ordering and restores its evidence control", async () => {
    const { router } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Full report" }));
    const parent = router.state.location.pathname + router.state.location.search;
    fireEvent.click(screen.getByRole("link", { name: "Evidence" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to report" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Evidence" })).toHaveFocus());
    expect(router.state.location.pathname + router.state.location.search).toBe(parent);
  });
  it("returns through nested manuscript/report/evidence with exact filters and origin focus", async () => {
    const { router } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    fireEvent.click(await screen.findByRole("link", { name: "Full report" }));
    expect(new URLSearchParams(router.state.location.search).get("flags_view")).toBe("high");
    fireEvent.click(screen.getByRole("link", { name: "Evidence" }));
    expect(router.state.location.state.callerValue).toBe("retained");
    expect(router.state.location.state.routeReturnFocus.returnPath).toContain("flags_view=high");
    fireEvent.click(screen.getByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open manuscript" })).toHaveFocus());
    expect(router.state.location.pathname + router.state.location.search).toBe(origin);
  });

  it("restores a prior-report action inside its disclosure", async () => {
    setup();
    fireEvent.click(screen.getByText("More actions"));
    fireEvent.click(screen.getByRole("link", { name: "Prior report" }));
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Prior report" })).toHaveFocus());
    expect(screen.getByText("More actions").parentElement).toHaveAttribute("open");
  });

  it("retains the original list and falls back to its heading when the action is gone", async () => {
    const { router, removeRecord } = setup();
    fireEvent.click(screen.getByRole("link", { name: "Open manuscript" }));
    removeRecord();
    fireEvent.click(await screen.findByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Review Desk" })).toHaveFocus());
    expect(router.state.location.pathname + router.state.location.search).toBe(origin);
  });

  it("uses URL context after a fresh page entry without routing state", async () => {
    const params = new URLSearchParams({ desk_return: origin, desk_focus: "review-desk-primary-16", finding: "38" });
    const { router } = setup(`/report/20/document?${params}`);
    expect(router.state.location.state).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Return to Review Desk" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open manuscript" })).toHaveFocus());
    expect(router.state.location.pathname + router.state.location.search).toBe(origin);
  });

  it("uses the ordinary safe default for a malicious direct return target", () => {
    setup("/report/20?desk_return=https%3A%2F%2Fevil.test&desk_focus=logout");
    expect(screen.getByRole("link", { name: "Return to Review Desk" })).toHaveAttribute("href", "/dashboard?queue=needs_review");
  });
});
