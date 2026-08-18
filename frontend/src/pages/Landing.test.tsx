import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { LandingRoute } from "./Landing";

const SIGNED_OUT = new Response(JSON.stringify({ error: { code: "unauthenticated", message: "x" } }), {
  status: 401,
});

describe("LandingRoute (screen 4v)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the landing page to a confirmed-anonymous visitor", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: /manuscript readiness checks for capstone instructors/i,
      }),
    ).toBeInTheDocument();
  });

  it("never renders landing content for an authenticated visitor (fast path to /dashboard)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/auth/me": { id: 1, email: "a@b.com", display_name: "A" } }),
    );
    renderWithProviders(<LandingRoute />);

    await waitFor(() =>
      expect(screen.queryByText(/manuscript readiness checks/i)).not.toBeInTheDocument(),
    );
  });

  it("V-067: exactly one 'Sign in' control on the page, routing to /signin", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });

    const signInLinks = screen.getAllByRole("link", { name: "Sign in" });
    expect(signInLinks).toHaveLength(1);
    expect(signInLinks[0]).toHaveAttribute("href", "/signin");
  });

  it("V-067: carries no readiness-tier-count claim (Track E P2-1, contradicted the dashboard's real 4-tile KPI row)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });

    expect(screen.queryByText(/readiness tiers/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/one of three statuses/i)).not.toBeInTheDocument();
  });

  it("carries no student-facing signup content (instructor-facing only, V7 blocked)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });

    expect(screen.queryByText(/sign up/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/create an account/i)).not.toBeInTheDocument();
  });

  it("the header logo is a real link back to the landing page itself (consistency with sign-in/dashboard)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: "VERIDICAL" })).toHaveAttribute("href", "/");
  });
});
