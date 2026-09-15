import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { NotFoundPage } from "./NotFound";

const SIGNED_OUT = new Response(JSON.stringify({ error: { code: "unauthenticated", message: "x" } }), {
  status: 401,
});

describe("NotFoundPage (BUG-222)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the app's own not-found screen, not a dev error page", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<NotFoundPage />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "This page doesn't exist." }),
    ).toBeInTheDocument();
  });

  it("offers sign-in for a signed-out visitor", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<NotFoundPage />);

    const link = await screen.findByRole("link", { name: "Go to sign in" });
    expect(link).toHaveAttribute("href", "/signin");
  });

  it("offers the Review Desk for a signed-in instructor", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/auth/me": { id: 1, email: "a@b.com", display_name: "A" } }),
    );
    renderWithProviders(<NotFoundPage />);

    const link = await screen.findByRole("link", { name: "Go to Review Desk" });
    expect(link).toHaveAttribute("href", "/dashboard");
  });
});
