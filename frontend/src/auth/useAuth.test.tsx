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
});
