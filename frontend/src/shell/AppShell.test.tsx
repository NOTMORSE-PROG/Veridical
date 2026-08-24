import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { RequireAuth } from "../auth/RequireAuth";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AppShell } from "./AppShell";

// Desktop nav + the mobile disclosure panel's own nav both render in the
// DOM at once (the panel is toggled via the `hidden` attribute, not
// unmounted) — every nav-item query here expects TWO matches, not one.
// The mobile panel's account block ("Sign out") is inside that same
// `hidden`-gated container, so it's correctly excluded from role queries
// while the panel is closed (jsdom respects the native `hidden`
// attribute, unlike Tailwind's `md:hidden` classes which need real CSS
// media-query support jsdom doesn't have).

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

  it("renders every real nav item — the same one shell every page uses (no per-page nav)", async () => {
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
    for (const label of ["Dashboard", "Rubric", "Library", "Audit log", "Settings"]) {
      expect(screen.getAllByText(label).length).toBe(2);
    }
  });

  it("nav redesign: no placeholder item advertises an unbuilt/unapproved feature (Submissions referenced the BLOCKED V7 student portal, D-005)", async () => {
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
    // "Archive"/"Settings" were excluded here too before V-042 shipped
    // them as real destinations (this file's own comment: "Archive/
    // Settings, V-042, are a materially different case" from the
    // disabled placeholders this test guards against) -- only the truly
    // unbuilt/unapproved ones stay excluded now. Archive itself was
    // retired and folded into Library, V-066; the nav slot's own history
    // still applies to it under its new name.
    for (const label of ["Submissions", "Soon"]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });

  it("BUG-024: the mobile account block (sign-out) lives inside the disclosure panel, reachable once opened", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    // queryByText ignores the native `hidden` attribute (it isn't an
    // accessible-tree query), so assert via role instead — this is the
    // same distinction the file's own top comment calls out.
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByText("Signed in as Demo Instructor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("WCAG 2.4.11 (found live, ux-critic): the panel closes when focus tabs past its last item, instead of leaving a focused control hidden underneath the still-open panel", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <button type="button">Page content</button>
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const signOut = screen.getByRole("button", { name: "Sign out" });
    const pageButton = screen.getByRole("button", { name: "Page content" });

    fireEvent.blur(signOut, { relatedTarget: pageButton });

    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("BUG-102 (WCAG 2.4.11): activating the skip link closes the mobile nav, not just moves focus underneath it", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();

    // The skip link sits BEFORE the panel in the DOM -- activating it
    // never passes focus through the panel, so the existing
    // handlePanelBlur mechanism (tested above) never fires. This is the
    // separate, generic click-capture fix.
    fireEvent.click(screen.getByRole("link", { name: "Skip to main content" }));

    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("BUG-102: any same-page anchor closes the mobile nav, not only the skip link", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <a href="#decision-heading">Go to final decision</a>
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Go to final decision" }));

    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("Shift+Tab back to the hamburger itself doesn't snap the panel shut", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
      { route: "/dashboard" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const signOut = screen.getByRole("button", { name: "Sign out" });
    const hamburger = screen.getByRole("button", { name: "Close menu" });

    fireEvent.blur(signOut, { relatedTarget: hamburger });

    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("BUG-009: signing out clears the whole query cache, not just the auth query (a shared-machine cross-instructor leak otherwise)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
        "/auth/logout": {},
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    // A stand-in for another instructor-scoped query (dashboard stats,
    // manuscript lists) that should NOT survive logout on a shared browser.
    queryClient.setQueryData(["dashboard-stats"], { readyCount: 3 });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <AppShell>
            <div />
          </AppShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Sign out/ }));
    // A still-mounted query (useMe/useQuota) refetches immediately after a
    // clear, which is correct — the proof of the fix is that the OTHER
    // instructor's stale cached query is gone for good, not that the cache
    // stays empty forever.
    await waitFor(() => expect(queryClient.getQueryData(["dashboard-stats"])).toBeUndefined());
    expect(
      queryClient.getQueryCache().findAll({ queryKey: ["dashboard-stats"] }),
    ).toHaveLength(0);
  });

  it("BUG-036: signing out immediately unmounts the currently-active protected screen, instead of leaving its content on screen until the next unrelated navigation", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
        "/auth/logout": {},
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/report/25"]}>
          <Routes>
            <Route path="/signin" element={<div>Sign-in page</div>} />
            <Route
              path="/report/:id"
              element={
                <RequireAuth>
                  <AppShell>
                    <div>Real manuscript excerpt content</div>
                  </AppShell>
                </RequireAuth>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    expect(screen.getByText("Real manuscript excerpt content")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Sign out/ }));

    await waitFor(() => expect(screen.getByText("Sign-in page")).toBeInTheDocument());
    expect(screen.queryByText("Real manuscript excerpt content")).not.toBeInTheDocument();
  });

  it("the header logo is a real link back to the dashboard (found live: it was plain text everywhere in the app)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/quota": QUOTA,
      }),
    );
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
      { route: "/rubric" },
    );
    await waitFor(() => expect(screen.getByText("DI")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "VERIDICAL" })).toHaveAttribute("href", "/dashboard");
  });
});
