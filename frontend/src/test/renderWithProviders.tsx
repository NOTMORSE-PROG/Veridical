// Shared test harness: every V-014+ screen needs react-query + a router.
// A fresh QueryClient per render avoids cache bleed between tests.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

export function renderWithProviders(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Routes a stubbed `fetch` by pathname → JSON body (or a Response). */
export function stubFetchByPath(handlers: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = new URL(url, "http://localhost").pathname;
    const handler = handlers[path];
    if (handler === undefined) {
      return new Response(JSON.stringify({ error: { code: "internal", message: "no handler" } }), {
        status: 404,
      });
    }
    if (handler instanceof Response) return handler;
    return new Response(JSON.stringify(handler), { status: 200 });
  });
}
