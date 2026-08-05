import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AppShell } from "./AppShell";

// Desktop nav + the mobile disclosure panel's own nav both render in the
// DOM at once (the panel is toggled via the `hidden` attribute, not
// unmounted) — every nav-item query here expects TWO matches, not one.

const QUOTA = {
  mode: "fake",
  quota_day: "2026-07-25",
  calls_used: 0,
  daily_limit: 1400,
  calls_remaining: 1400,
  cache_hits_today: 0,
  cache_hit_rate: 0,
  reset_at: "2026-07-26T00:00:00-07:00",
  rpm_limit: 12,
};

describe("AppShell", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the signed-in instructor's initials and a real (not invented) quota direction", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div>content</div>
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    // BUG-017: never a bare, directionless percentage. Rendered twice — a
    // visible (mobile-hidden) span and an sr-only span carry the same
    // full sentence, since a short mobile badge ("100% left") replaces it
    // visually below the sm: breakpoint but the accessible name stays full.
    expect(screen.getAllByText("AI capacity: 100% remaining today").length).toBe(2);
  });

  it("BUG-017: shows an unambiguous 'no capacity left' state, not a bare 0%", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": { ...QUOTA, calls_used: 1400, calls_remaining: 0 },
      }),
    );
    renderWithProviders(
      <AppShell>
        <div>content</div>
      </AppShell>,
      { route: "/dashboard" },
    );
    expect((await screen.findAllByText("No AI capacity left today")).length).toBe(2);
  });

  it("marks the active nav link with aria-current, not just a class (BUG-015 area)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": new Response(JSON.stringify({ error: {} }), { status: 401 }),
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
      { route: "/dashboard" },
    );
    // Only the desktop nav's link is in the accessibility tree right now —
    // the mobile panel is closed (`hidden`), correctly excluding its
    // duplicate copy from getByRole (unlike getByText, which doesn't check
    // accessibility-tree visibility and would find both).
    const dashboardLink = await screen.findByRole("link", { name: "Dashboard" });
    expect(dashboardLink).toHaveAttribute("aria-current", "page");
  });

  it("BUG-015: not-yet-shipped nav items are marked aria-disabled with a 'Soon' tag, not just styled differently", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": new Response(JSON.stringify({ error: {} }), { status: 401 }),
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    );
    const [settingsItem] = screen.getAllByText("Settings");
    const wrapper = settingsItem.closest("span[aria-disabled]");
    expect(wrapper).toHaveAttribute("aria-disabled", "true");
    expect(wrapper).toHaveTextContent("Soon");
  });

  it("renders every nav item — the same one shell every page uses (no per-page nav)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": new Response(JSON.stringify({ error: {} }), { status: 401 }),
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    );
    for (const label of ["Dashboard", "Rubric", "Submissions", "Archive", "Audit log", "Settings"]) {
      expect(screen.getAllByText(label).length).toBe(2);
    }
  });
});
