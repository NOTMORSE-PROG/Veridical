import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalShell } from "./SignalShell";

function renderShell(route = "/library") {
  vi.stubGlobal(
    "fetch",
    stubFetchByPath({
      "/auth/session": {
        instructor: {
          id: 1,
          email: "instructor@demo.local",
          display_name: "Demo Instructor",
          onboarding_dismissed_at: "2026-01-01T00:00:00Z",
        },
      },
      "/quota": { calls_used: 3, calls_remaining: 17, daily_limit: 20 },
    }),
  );
  return renderWithProviders(<SignalShell><p>Route content</p></SignalShell>, { route });
}

describe("SignalShell destination navigation", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("exposes every stable destination in the primary navigation", async () => {
    renderShell();
    const navigation = await screen.findByRole("navigation", { name: "Primary navigation" });

    expect(within(navigation).getByRole("link", { name: "Review Desk" })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Required format" })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("link", { name: "Audit" })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Work stages" })).not.toBeInTheDocument();
  });

  it("uses one primary navigation without an alternate device menu", async () => {
    renderShell();
    await screen.findByRole("navigation", { name: "Primary navigation" });
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("keeps account actions accessible and restores focus on Escape", async () => {
    renderShell();
    const account = await screen.findByRole("button", { name: /Account/ });
    fireEvent.click(account);
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    screen.getByRole("button", { name: "Replay introduction" }).focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(account).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
