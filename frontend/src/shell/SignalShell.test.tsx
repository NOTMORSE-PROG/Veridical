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
      "/quota": { mode: "live", key_state: "configured", calls_used: 3, calls_remaining: 17, daily_limit: 20 },
    }),
  );
  return renderWithProviders(<SignalShell><p>Route content</p></SignalShell>, { route });
}

describe("SignalShell destination navigation", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["unavailable", 17, "AI setup unavailable"], ["unavailable", 0, "AI setup unavailable"],
    ["not_checked", 17, "AI setup status unknown"], [undefined, 17, "AI setup status unknown"],
    ["future", 17, "AI setup status unknown"], ["configured", 0, "Local AI budget reached"],
  ])("prioritizes setup state %s over remaining budget %s", async (key_state, calls_remaining, text) => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/session": { instructor: { id: 1, email: "instructor@demo.local", display_name: "Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" } },
      "/quota": { mode: "live", key_state, calls_remaining },
    }));
    renderWithProviders(<SignalShell><p>Route content</p></SignalShell>);
    fireEvent.click(await screen.findByRole("button", { name: "Account" }));
    expect((await screen.findAllByText(text as string)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/17 remaining/)).not.toBeInTheDocument();
  });

  it.each(["fake", "unknown"])("does not advertise available provider requests for %s budget data", async state => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/auth/session": { instructor: { id: 1, email: "instructor@demo.local", display_name: "Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" } },
      "/quota": { mode: state === "fake" ? "fake" : "live", calls_remaining: state === "fake" ? 300 : null },
    }));
    renderWithProviders(<SignalShell><p>Route content</p></SignalShell>);
    fireEvent.click(await screen.findByRole("button", { name: "Account" }));
    await screen.findAllByText(state === "fake" ? "Test mode" : "AI setup status unknown");
    expect(screen.queryByText(/300 remaining/)).not.toBeInTheDocument();
    expect(screen.queryByText(/calls left/)).not.toBeInTheDocument();
  });

  it.each(["available", "loading", "unavailable"])("keeps %s capacity accessible through Account using reads only", async (state) => {
    const base = stubFetchByPath({ "/auth/session": { instructor: { id: 1, email: "instructor@demo.local", display_name: "Demo Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" } }, "/quota": { mode: "live", key_state: "configured", calls_used: 3, calls_remaining: 17, daily_limit: 20 } });
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

  it.each(["Account"])("V-081: replay from Settings survives closing its %s menu", async (triggerName) => {
    const me = { id: 1, email: "instructor@demo.local", display_name: "Instructor", onboarding_dismissed_at: "2026-01-01T00:00:00Z" };
    const fetchMock = stubFetchByPath({
      "/auth/session": { instructor: me },
      "/auth/onboarding/replay": { ...me, onboarding_dismissed_at: null },
      "/quota": { calls_used: 3, calls_remaining: 17, daily_limit: 20 },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SignalShell><p>Route content</p><button type="button" data-tour="upload-format-cta">Upload required format</button></SignalShell>, { route: "/settings" });
    const trigger = await screen.findByRole("button", { name: triggerName });
    trigger.getBoundingClientRect = () => ({ top: 100, left: 100, width: 120, height: 44, bottom: 144, right: 220 }) as DOMRect;
    screen.getByRole("button", { name: "Upload required format" }).getBoundingClientRect = trigger.getBoundingClientRect;
    fireEvent.click(trigger);
    const replay = screen.getByRole("button", { name: "Replay introduction" });
    replay.getBoundingClientRect = trigger.getBoundingClientRect;
    fireEvent.click(replay);
    // BUG-303: replay starts the introduction now, at step 1, on the Review
    // Desk (this harness renders the shell on every path).
    expect(await screen.findByRole("dialog", { name: "Format comes first" })).toBeInTheDocument();
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

  it("keeps one desktop navigation and Account without a mobile menu", async () => {
    renderShell();
    expect(await screen.findByRole("button", { name: "Account" })).toBeInTheDocument();
    expect(screen.getAllByRole("navigation", { name: "Primary navigation" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Menu" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Mobile navigation" })).not.toBeInTheDocument();
  });

  it("marks the destination after using the desktop navigation", async () => {
    renderShell();
    const navigation = await screen.findByRole("navigation", { name: "Primary navigation" });
    fireEvent.click(within(navigation).getByRole("link", { name: "Audit" }));
    await waitFor(() => expect(within(navigation).getByRole("link", { name: "Audit" })).toHaveAttribute("aria-current", "page"));
  });

  it("reveals the full desktop destination label when keyboard focus enters an overflowing navigation", async () => {
    renderShell();
    const navigation = await screen.findByRole("navigation", { name: "Primary navigation" });
    const destination = within(navigation).getByRole("link", { name: "Settings" });
    const reveal = vi.fn(); Object.defineProperty(destination, "scrollIntoView", { configurable: true, value: reveal });
    destination.focus();
    expect(reveal).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
    expect(destination).toHaveFocus();
  });

  it("closes Account on Escape and restores keyboard focus", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Account" });
    trigger.focus(); fireEvent.click(trigger);
    screen.getByRole("button", { name: "Sign out" }).focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("closes Account when keyboard focus leaves its final action", async () => {
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Account" });
    fireEvent.click(trigger);
    fireEvent.blur(screen.getByRole("button", { name: "Sign out" }), { relatedTarget: screen.getByText("Route content") });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});
