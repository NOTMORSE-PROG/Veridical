import { act, createEvent, fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { Modal, ModalBackdrop } from "../components/Modal";
import { CoachMark } from "./CoachMark";
import { TourProvider, useTour } from "./TourContext";
import { useMe } from "../auth/useAuth";
import { registerTaskModal, taskModalActive } from "../components/modalActivity";

// Mirrors AppShell's real ReplayTourButton: a SEPARATE component from
// CoachMark that calls useTour().replay() -- both reading the same
// TourProvider instance, which is exactly the integration the real bug
// lived in (two independently-instantiated useTour() calls, only one of
// which ever got its local "just dismissed" flag reset).
function ReplayButtonFixture() {
  const tour = useTour();
  return (
    <button type="button" data-tour="replay-tour-desktop" onClick={tour.replay}>
      Replay
    </button>
  );
}

// A stand-in "page" carrying the same data-tour anchors the real
// screens carry -- CoachMark itself is screen-agnostic, so testing it
// against a minimal fixture (rather than mounting the whole Dashboard)
// keeps these tests about the tour engine, not about Dashboard.tsx.
function DashboardFixture() {
  return (
    <div>
      <button data-tour="upload-format-cta">Upload required format</button>
      <button data-tour="new-check-cta">New check</button>
      <button data-tour="replay-tour-desktop">Replay</button>
    </div>
  );
}

function EntryDialogFixture({ onEntry }: { onEntry: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button data-tour="upload-format-cta" onClick={() => { onEntry(); setOpen(true); }}>
        Upload required format
      </button>
      {open && <ModalBackdrop><Modal title="Upload required format" onClose={() => setOpen(false)}>
        <button onClick={() => setOpen(false)}>Cancel</button>
      </Modal></ModalBackdrop>}
    </>
  );
}

function stubRect(el: Element) {
  el.getBoundingClientRect = () =>
    ({ top: 100, left: 100, width: 120, height: 44, bottom: 144, right: 220 }) as DOMRect;
}

function mountFixtureRects() {
  for (const el of document.querySelectorAll("[data-tour]")) stubRect(el);
}

const ME_NOT_DISMISSED = {
  id: 1,
  email: "a@b.com",
  display_name: "Demo Instructor",
  onboarding_dismissed_at: null,
};
const ME_DISMISSED = {
  ...ME_NOT_DISMISSED,
  onboarding_dismissed_at: "2026-08-06T00:00:00Z",
};

function SessionReadyFixture() {
  const { data } = useMe();
  return <output data-testid="guide-session-ready">{data?.id ?? "loading"}</output>;
}

describe("task modal and late guide coordination", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps a chosen task and its focus when undismissed instructor data arrives later", async () => {
    let release = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    const ordinaryFetch = stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED });
    vi.stubGlobal("fetch", async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]).endsWith("/auth/me")) await held;
      return ordinaryFetch(...args);
    });
    renderWithProviders(<TourProvider>
      <SessionReadyFixture />
      <EntryDialogFixture onEntry={() => {}} />
      <CoachMark />
    </TourProvider>, { route: "/dashboard" });
    mountFixtureRects();
    fireEvent.click(screen.getByRole("button", { name: "Upload required format" }));
    const task = screen.getByRole("dialog", { name: "Upload required format" });
    const taskFocus = document.activeElement;
    expect(task.contains(taskFocus)).toBe(true);
    await act(async () => { release(); });
    await waitFor(() => expect(screen.getByTestId("guide-session-ready")).toHaveTextContent("1"));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Format comes first" })).not.toBeInTheDocument();
    expect(document.activeElement).toBe(taskFocus);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Format comes first" })).toBeInTheDocument());
  });

  it("waits for every task lifetime and tolerates repeated cleanup", () => {
    let first = () => {};
    let second = () => {};
    act(() => { first = registerTaskModal(); second = registerTaskModal(); });
    try {
      expect(taskModalActive()).toBe(true);
      act(() => { first(); first(); });
      expect(taskModalActive()).toBe(true);
      act(() => second());
      expect(taskModalActive()).toBe(false);
    } finally {
      act(() => { first(); second(); });
    }
  });
});

describe("CoachMark", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not render when the instructor has already dismissed/completed the tour", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    // Give the me-query a tick to resolve either way.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("renders the first resolvable step, anchored, with focus moved into it", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Format comes first");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });

  it("V-081: safe entry releases the introduction before opening the ordinary dialog and restores focus on Cancel", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    const onEntry = vi.fn(() => expect(document.getElementById("root")).not.toHaveAttribute("inert"));
    renderWithProviders(<div id="root"><TourProvider>
      <EntryDialogFixture onEntry={onEntry} />
      <CoachMark />
    </TourProvider></div>, { route: "/dashboard" });
    mountFixtureRects();
    const entry = await screen.findByRole("button", { name: "Open format upload" });
    expect(document.getElementById("root")).toHaveAttribute("inert");
    fireEvent.click(entry);

    const dialog = await screen.findByRole("dialog", { name: "Upload required format" });
    expect(onEntry).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog", { name: "Format comes first" })).not.toBeInTheDocument();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.getElementById("root")).toHaveAttribute("inert");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.getElementById("root")).not.toHaveAttribute("inert");
    expect(screen.getByRole("button", { name: "Upload required format" })).toHaveFocus();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([input]) =>
      new URL(input.toString(), "http://localhost").pathname)).toEqual(["/auth/onboarding/dismiss"]);
  });

  it("V-081: confirmation steps never offer automatic entry or activate the highlighted action", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    const confirm = vi.fn();
    renderWithProviders(<TourProvider>
      <button data-tour="confirm-rubric-cta" onClick={confirm}>Confirm rubric</button>
      <CoachMark />
    </TourProvider>, { route: "/rubric/1/review" });
    mountFixtureRects();
    // BUG-091: the tour is sequential -- step 1 renders first (unanchored,
    // its control lives on the Review Desk), then this screen's step.
    await screen.findByRole("dialog", { name: "Format comes first" });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("dialog", { name: "Review before you confirm" });
    expect(screen.queryByRole("button", { name: "Open format upload" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Choose manuscript" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("V-081: a disabled upload control cannot be invoked through the introduction", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    const open = vi.fn();
    renderWithProviders(<TourProvider>
      <button disabled data-tour="upload-format-cta" onClick={open}>Upload required format</button>
      <CoachMark />
    </TourProvider>, { route: "/dashboard" });
    mountFixtureRects();
    const entry = await screen.findByRole("button", { name: "Open format upload" });
    expect(entry).toBeDisabled();
    fireEvent.click(entry);
    expect(open).not.toHaveBeenCalled();
  });

  it("Next advances to the next step, even when its control is on another screen (BUG-091)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // Step 2 (confirm-rubric) lives on the format review screen. It is no
    // longer skipped: it renders unanchored and says where it lives.
    const dialog = await screen.findByRole("dialog", { name: "Review before you confirm" });
    expect(dialog).toHaveTextContent("format review screen");
    expect(screen.getByText(/^Step 2 of 5: Review before you confirm$/)).toBeInTheDocument();
    expect(screen.getByTestId("coachmark-scrim")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Run a check when you're ready"));
    expect(screen.queryByTestId("coachmark-scrim")).not.toBeInTheDocument();
  });

  it("BUG-091: a day-one account walks all five steps in order under an honest counter", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <button data-tour="upload-format-cta">Upload required format</button>
        <button data-tour="new-check-cta">New check</button>
        <button data-tour="replay-tour-account">Account</button>
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");
    const seen: string[] = [];
    for (let step = 1; step <= 5; step++) {
      const announcement = await screen.findByText(new RegExp(`^Step ${step} of 5: `));
      seen.push(announcement.textContent ?? "");
      fireEvent.click(screen.getByRole("button", { name: step === 5 ? "Done" : "Next" }));
    }
    expect(seen).toEqual([
      "Step 1 of 5: Format comes first",
      "Step 2 of 5: Review before you confirm",
      "Step 3 of 5: Run a check when you're ready",
      "Step 4 of 5: This is yours to decide",
      "Step 5 of 5: Come back anytime",
    ]);
  });

  it("Back is hidden on the first resolved step", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  it("Escape dismisses the tour and persists it server-side", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/onboarding/dismiss"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("the close (x) button dismisses the tour the same way Escape does", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");

    fireEvent.click(screen.getByRole("button", { name: "Close introduction" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/onboarding/dismiss"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("a step whose control never mounts on its own screen renders unanchored after bounded discovery (AC5)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    // This fixture has no "upload-format-cta" at all -- the tour must not
    // render a broken pointer at step 1, and must not silently skip it.
    renderWithProviders(
      <TourProvider>
        <button data-tour="new-check-cta">New check</button>
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog", {}, { timeout: 7000 });
    expect(dialog).toHaveTextContent("Format comes first");
    expect(dialog).toHaveTextContent("You will find this on the Review Desk.");
    expect(screen.getByTestId("coachmark-scrim")).toBeInTheDocument();
    // Entry actions need a real control; an unanchored step never offers one.
    expect(screen.queryByRole("button", { name: "Open format upload" })).not.toBeInTheDocument();
  }, 10000);

  it("BUG-303: Replay introduction from another screen starts now, on the Review Desk", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/me": ME_DISMISSED,
      "/auth/onboarding/replay": ME_NOT_DISMISSED,
    }));
    function LocationProbe() {
      return <output data-testid="pathname">{useLocation().pathname}</output>;
    }
    function RouteAwareDesk() {
      const onDesk = useLocation().pathname === "/dashboard";
      return onDesk ? <DashboardFixture /> : null;
    }
    renderWithProviders(
      <TourProvider>
        <RouteAwareDesk />
        <ReplayButtonFixture />
        <LocationProbe />
        <CoachMark />
      </TourProvider>,
      { route: "/rubric" },
    );
    await screen.findByRole("button", { name: "Replay" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    await waitFor(() => expect(screen.getByTestId("pathname")).toHaveTextContent("/dashboard"));
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog", { name: "Format comes first" });
    expect(dialog).not.toHaveTextContent("You will find this on the Review Desk.");
  });

  it("Done on the final (replay) step dismisses the tour", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");
    for (let step = 1; step < 5; step++) fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Come back anytime");
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/onboarding/dismiss"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  // Regression test for the real bug UX review found live: a Replay
  // button that called its OWN useReplayOnboarding() mutation directly
  // (bypassing useTour()'s replayTour(), the only thing that reset the
  // local dismissedLocally flag) silently did nothing on the second-or-
  // later Replay click in one session -- only a full page reload worked.
  // This renders a replay trigger as its own component (not CoachMark
  // itself), exactly like AppShell's real ReplayTourButton, sharing one
  // TourProvider -- the shape the bug actually lived in.
  it("replaying after a dismiss re-shows the tour without a page reload (regression: shared tour state)", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
      "/auth/onboarding/replay": ME_NOT_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <TourProvider>
        <button data-tour="upload-format-cta">Upload required format</button>
        <ReplayButtonFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");

    fireEvent.click(screen.getByRole("button", { name: "Close introduction" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Format comes first"));
  });

  it("BUG-581: a tour not yet open stays closed once the instructor starts using the page, and Replay still opens it", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/replay": ME_NOT_DISMISSED,
    }));
    // The Review Desk's controls arrive with its data, after the session.
    function LateDesk() {
      const [loaded, setLoaded] = useState(false);
      return <>
        <button type="button" onClick={() => setLoaded(true)}>Open my groups</button>
        {loaded && <button data-tour="upload-format-cta">Upload required format</button>}
      </>;
    }
    renderWithProviders(
      <TourProvider>
        <SessionReadyFixture />
        <LateDesk />
        <ReplayButtonFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByTestId("guide-session-ready")).toHaveTextContent("1"));
    const ownClick = screen.getByRole("button", { name: "Open my groups" });
    fireEvent.pointerDown(ownClick);
    fireEvent.click(ownClick);
    mountFixtureRects();
    // The desk's controls are there, but the instructor is already using it.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload required format" })).toBeInTheDocument();

    fireEvent.pointerDown(screen.getByRole("button", { name: "Replay" }));
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Format comes first");
    // Using the opened tour does not close it.
    fireEvent.pointerDown(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("BUG-581: focus moved out of the open tour by the page comes back to it", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <h1 tabIndex={-1}>Review Desk</h1>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    // As the page does after sign-in: it focuses its own heading.
    act(() => screen.getByRole("heading", { name: "Review Desk" }).focus());
    expect(dialog.contains(document.activeElement)).toBe(true);
    // Focus dropped to the page body (its control became inert) also returns.
    act(() => (document.activeElement as HTMLElement).blur());
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });

  it("BUG-581: a press outside the open tour keeps focus in it", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const outside = createEvent.mouseDown(document.body);
    fireEvent(document.body, outside);
    expect(outside.defaultPrevented).toBe(true);
    expect(dialog.contains(document.activeElement)).toBe(true);
    const inside = createEvent.mouseDown(screen.getByRole("button", { name: "Next" }));
    fireEvent(screen.getByRole("button", { name: "Next" }), inside);
    expect(inside.defaultPrevented).toBe(false);
  });

  // BUG-063 item 2: "Back" used to be enabled and do nothing. Confirmed
  // live 2026-09-02 that it now moves the step index -- this locks that in
  // as a regression test rather than relying on the live recheck alone.
  it("Back returns to the previous resolved step (BUG-063 item 2)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toHaveTextContent("Review before you confirm"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Format comes first"));
  });

  // BUG-063 item 3: the step dots carried position by size/colour alone,
  // no text alternative (WCAG 1.4.1). CoachMark() already renders a
  // sr-only aria-live announcement of "Step N of M: <title>" alongside the
  // dots -- this asserts it exists and updates, since no prior test named it.
  it("announces the step number and title to assistive tech (BUG-063 item 3)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    renderWithProviders(
      <TourProvider>
        <DashboardFixture />
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    await screen.findByText("Format comes first");

    expect(screen.getByText(/^Step 1 of \d+: Format comes first$/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(screen.getByText(/^Step 2 of 5: Review before you confirm$/)).toBeInTheDocument(),
    );
  });
});
