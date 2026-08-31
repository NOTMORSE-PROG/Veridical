import { fireEvent, screen, waitFor } from "@testing-library/react";
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
        name: /check the manuscript\. keep the decision human\./i,
      }),
    ).toBeInTheDocument();
  });

  it("BUG-186: maps the clean session-status response to the public landing state", async () => {
    const fetchMock = stubFetchByPath({ "/auth/session": { instructor: null } });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<LandingRoute />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "Check the manuscript. Keep the decision human.",
    );
    expect(new URL(String(fetchMock.mock.calls[0][0]), "http://localhost").pathname).toBe(
      "/auth/session",
    );
  });

  it("never renders landing content for an authenticated visitor (fast path to /dashboard)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/auth/me": { id: 1, email: "a@b.com", display_name: "A" } }),
    );
    renderWithProviders(<LandingRoute />);

    await waitFor(() =>
      expect(screen.queryByText(/check the manuscript/i)).not.toBeInTheDocument(),
    );
  });

  it("V-073: has exactly one instructor sign-in action, routing to /signin", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });

    const signInLinks = screen.getAllByRole("link", { name: "Sign in as instructor" });
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

  it("states the T.I.P. project boundary and the complete four-stage review path", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });

    expect(
      screen.getAllByText(/not an official T\.I\.P\. service/i).length,
    ).toBeGreaterThan(0);
    for (const stage of ["Prepare", "Check", "Review", "Decide"]) {
      expect(screen.getByRole("heading", { level: 3, name: stage })).toBeInTheDocument();
    }
  });

  it("retries the auth query in place after a temporary service failure", async () => {
    let requests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        requests += 1;
        if (requests === 1) {
          return new Response(
            JSON.stringify({ error: { code: "api_down", message: "Unavailable" } }),
            { status: 503 },
          );
        }
        return new Response(
          JSON.stringify({ error: { code: "unauthenticated", message: "x" } }),
          { status: 401 },
        );
      }),
    );
    renderWithProviders(<LandingRoute />);

    expect(
      await screen.findByRole("heading", { name: "VERIDICAL is temporarily unavailable." }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: /check the manuscript\. keep the decision human\./i,
      }),
    ).toBeInTheDocument();
    expect(requests).toBe(2);
  });

  it("the header logo is a real link back to the landing page itself (consistency with sign-in/dashboard)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<LandingRoute />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: "VERIDICAL home" })).toHaveAttribute("href", "/");
  });
});
