// Shared test harness: every V-014+ screen needs react-query + a router.
// A fresh QueryClient per render avoids cache bleed between tests.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

export function renderWithProviders(
  ui: ReactElement,
  {
    route = "/",
    path,
    state,
  }: { route?: string; path?: string; state?: Record<string, unknown> } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // A data router (createMemoryRouter), not plain MemoryRouter/Routes:
  // react-router's navigation-blocking hook (useBlocker, BUG-037) only
  // works inside a data router, and every screen under test must render
  // in the same kind of router the real app now uses (App.tsx). `path`
  // matches a dynamic segment (e.g. "/rubric/:rubricId/review") so
  // useParams() resolves inside the test, same as the real router does;
  // "*" (no `path` given) matches unconditionally, same as the old
  // bare-`ui`-with-no-Route-boundary fallback did.
  const entry = state ? { pathname: route, state } : route;
  const router = createMemoryRouter([{ path: path ?? "*", element: ui }], {
    initialEntries: [entry],
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** Routes a stubbed `fetch` by pathname → JSON body (or a Response). */
export function stubFetchByPath(handlers: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = new URL(url, "http://localhost").pathname;
    // Auth screen fixtures written before BUG-186 use `/auth/me`. The live
    // query now uses the clean `/auth/session` projection; keeping this alias
    // lets those tests retain their signed-in/signed-out payload intent while
    // focused BUG-186 tests exercise the new response shape directly.
    const exactHandler = handlers[path];
    const legacyAuthHandler = path === "/auth/session" ? handlers["/auth/me"] : undefined;
    const handler = exactHandler ?? legacyAuthHandler;
    if (handler === undefined) {
      return new Response(JSON.stringify({ error: { code: "internal", message: "no handler" } }), {
        status: 404,
      });
    }
    if (handler instanceof Response) return handler;
    const body = exactHandler === undefined && legacyAuthHandler !== undefined
      ? { instructor: handler }
      : handler;
    return new Response(JSON.stringify(body), { status: 200 });
  });
}
