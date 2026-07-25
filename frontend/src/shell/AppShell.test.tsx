import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AppShell } from "./AppShell";

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

  it("shows the signed-in instructor's initials and a real (not invented) quota number", async () => {
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
    expect(screen.getByText("Quota 0%")).toBeInTheDocument();
  });

  it("underlines the active nav link for the current route", async () => {
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
    const dashboardLink = await screen.findByRole("link", { name: "Dashboard" });
    expect(dashboardLink.className).toMatch(/border-primary/);
  });

  it("renders every nav item every page uses the same one shell (no per-page nav)", async () => {
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
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
