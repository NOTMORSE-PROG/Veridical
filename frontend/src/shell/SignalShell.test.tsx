import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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

  it("opens one mobile menu with the same destinations and useful descriptions", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Menu" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const navigation = screen.getByRole("navigation", { name: "Mobile navigation" });
    expect(within(navigation).getByRole("link", { name: /Review Desk/ })).toBeInTheDocument();
    expect(within(navigation).getByText("Manuscripts and work that need attention")).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: /Library/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText(/Signed in as/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("closes the mobile menu after navigation and marks the new destination", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Menu" });
    fireEvent.click(trigger);
    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Mobile navigation" })).getByRole("link", {
        name: /Audit/,
      }),
    );

    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "false"));
    expect(screen.queryByRole("navigation", { name: "Mobile navigation" })).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole("link", {
        name: "Audit",
      }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("closes the mobile menu on Escape and restores focus to its trigger", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Menu" });
    fireEvent.click(trigger);
    within(screen.getByRole("navigation", { name: "Mobile navigation" }))
      .getByRole("link", { name: /Library/ })
      .focus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });
});
