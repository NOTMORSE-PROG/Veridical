import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { CoachMark } from "./CoachMark";
import { TourProvider, useTour } from "./TourContext";

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

  it("Next advances to the next resolvable step", async () => {
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
    // Step 2 (confirm-rubric) has no anchor in this fixture -- resolution
    // should skip straight to step 3 (new-check), never getting stuck.
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Run a check when you're ready"));
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

    fireEvent.click(screen.getByRole("button", { name: "Close tour" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/onboarding/dismiss"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("skips a step with no anchor on the page and lands on the next one that resolves (AC5)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": ME_NOT_DISMISSED }));
    // This fixture has no "upload-format-cta" at all -- resolution must
    // not render a broken pointer at step 1, it must fall through.
    renderWithProviders(
      <TourProvider>
        <button data-tour="new-check-cta">New check</button>
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Run a check when you're ready");
  });

  it("Done on the final (replay) step dismisses the tour", async () => {
    const fetchMock = stubFetchByPath({
      "/auth/me": ME_NOT_DISMISSED,
      "/auth/onboarding/dismiss": ME_DISMISSED,
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <TourProvider>
        <button data-tour="replay-tour-desktop">Replay</button>
        <CoachMark />
      </TourProvider>,
      { route: "/dashboard" },
    );
    mountFixtureRects();
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

  // Regression test for the real bug ux-critic found live: a Replay
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

    fireEvent.click(screen.getByRole("button", { name: "Close tour" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Format comes first"));
  });
});
