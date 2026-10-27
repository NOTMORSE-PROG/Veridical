import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY, useLogin } from "./useAuth";

describe("authentication cache boundaries", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("BUG-183: clears the previous instructor's cache before installing the new identity", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(["library", 1], { items: ["Instructor A manuscript"] });
    queryClient.setQueryData(["report", 41], { group_label: "Instructor A group" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            id: 2,
            email: "instructor-b@tip.edu.ph",
            display_name: "Instructor B",
            onboarding_dismissed_at: null,
          }),
          { status: 200 },
        )),
    );
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useLogin(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ email: "instructor-b@tip.edu.ph", password: "secret" });
    });

    expect(queryClient.getQueryData(["library", 1])).toBeUndefined();
    expect(queryClient.getQueryData(["report", 41])).toBeUndefined();
    expect(queryClient.getQueryData(ME_QUERY_KEY)).toMatchObject({
      id: 2,
      email: "instructor-b@tip.edu.ph",
    });
  });

  it("BUG-414: AccountDisclosure shows both the display name and signed-in email address", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { MemoryRouter } = await import("react-router");
    const { SignalShell } = await import("../shell/SignalShell");

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(ME_QUERY_KEY, {
      id: 7,
      email: "prof.santos@tip.edu.ph",
      display_name: "Prof. Santos",
      onboarding_dismissed_at: "2026-01-01T00:00:00Z",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ mode: "fake", calls_remaining: 500, key_state: "configured" }),
          { status: 200 },
        )),
    );

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <SignalShell>
            <div>Desk content</div>
          </SignalShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const trigger = screen.getByRole("button", { name: /Account/i });
    fireEvent.click(trigger);

    expect(screen.getByText("Prof. Santos")).toBeInTheDocument();
    expect(screen.getByText("prof.santos@tip.edu.ph")).toBeInTheDocument();
    expect((await screen.findAllByText("Test mode")).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/calls left/i)).not.toBeInTheDocument();
  });

  it("BUG-415: SignalShell names the daily budget 'Local AI budget' instead of an ambiguous 'calls left'", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { MemoryRouter } = await import("react-router");
    const { SignalShell } = await import("../shell/SignalShell");

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(ME_QUERY_KEY, {
      id: 8,
      email: "prof.reyes@tip.edu.ph",
      display_name: "Prof. Reyes",
      onboarding_dismissed_at: "2026-01-01T00:00:00Z",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ mode: "real", calls_remaining: 17, key_state: "configured" }),
          { status: 200 },
        )),
    );

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <SignalShell>
            <div>Desk content</div>
          </SignalShell>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Account/i }));

    expect(await screen.findByText("Local AI budget: 17 remaining")).toBeInTheDocument();
    expect(screen.queryByText(/calls left/i)).not.toBeInTheDocument();
  });
});
