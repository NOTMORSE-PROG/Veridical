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

  it.each(["fake", "unknown"])("does not advertise available provider requests for %s budget data", async state => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/session": { instructor: { id: 1, email: "instructor@demo.local", display_name: "Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" } },
      "/quota": { mode: state === "fake" ? "fake" : "live", calls_remaining: state === "fake" ? 300 : null },
    }));
    renderWithProviders(<SignalShell><p>Route content</p></SignalShell>);
    await screen.findAllByText(state === "fake" ? "Test mode" : "Local AI budget unavailable");
    expect(screen.queryByText(/300 remaining/)).not.toBeInTheDocument();
    expect(screen.queryByText(/calls left/)).not.toBeInTheDocument();
  });

  it.each(["available", "loading", "unavailable"])("keeps %s capacity accessible through Account using reads only", async (state) => {
    const base = stubFetchByPath({ "/auth/session": { instructor: { id: 1, email: "instructor@demo.local", display_name: "Demo Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" } }, "/quota": { calls_used: 3, calls_remaining: 17, daily_limit: 20 } });
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/quota")) {
        if (state === "loading") return await new Promise<Response>(() => {});
        if (state === "unavailable") return new Response(JSON.stringify({ error: { code: "unavailable", message: "Controlled unavailable quota" } }), { status: 503 });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SignalShell><p>Route content</p></SignalShell>);
    const account = await screen.findByRole("button", { name: "Account" }); account.focus(); fireEvent.click(account);
    const panel = document.getElementById("signal-account-panel")!;
    await within(panel).findByText(state === "available" ? /Local AI budget: 17 remaining/ : state === "loading" ? /Loading local AI budget/ : "Local AI budget unavailable");
    expect(account).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(account).toHaveFocus(); expect(account).toHaveAttribute("aria-expanded", "false");
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it.each(["Account", "Menu"])("V-081: replay from Settings survives closing its %s menu", async (triggerName) => {
    const me = { id: 1, email: "instructor@demo.local", display_name: "Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" };
    const fetchMock = stubFetchByPath({
      "/auth/session": { instructor: me },
      "/auth/onboarding/replay": { ...me, onboarding_dismissed_at: null },
      "/quota": { calls_used: 3, calls_remaining: 17, daily_limit: 20 },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalShell><p>Route content</p></SignalShell>, { route: "/settings" });
    const trigger = await screen.findByRole("button", { name: triggerName });
    trigger.getBoundingClientRect = () => ({ top: 100, left: 100, width: 120, height: 44, bottom: 144, right: 220 }) as DOMRect;
    fireEvent.click(trigger);
    const replay = screen.getByRole("button", { name: "Replay introduction" });
    replay.getBoundingClientRect = trigger.getBoundingClientRect;
    fireEvent.click(replay);
    expect(await screen.findByRole("dialog", { name: "Come back anytime" })).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([input]) =>
      new URL(input.toString(), "http://localhost").pathname)).toEqual(["/auth/onboarding/replay"]);
  });

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

  it("BUG-210 (WCAG 2.4.11): closes when focus tabs past its last item, instead of leaving a focused control hidden underneath the still-open panel", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Menu" });
    fireEvent.click(trigger);
    const signOut = screen.getByRole("button", { name: "Sign out" });
    const pageContent = screen.getByText("Route content");

    // Simulates what a real forward Tab past the panel's last focusable
    // item does: focus lands on the underlying page while the panel is
    // still mounted -- same mechanism BUG-102 fixed once already in the
    // predecessor AppShell (see AppShell.test.tsx's identical test).
    fireEvent.blur(signOut, { relatedTarget: pageContent });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
