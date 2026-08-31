import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { RequireAuth } from "./RequireAuth";

describe("RequireAuth", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders children once the session check confirms sign-in", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/auth/me": { id: 1, email: "a@b.com", display_name: "A" } }),
    );
    renderWithProviders(
      <RequireAuth>
        <div>Protected content</div>
      </RequireAuth>,
    );
    await waitFor(() => expect(screen.getByText("Protected content")).toBeInTheDocument());
  });

  it("never renders protected content when there is no session (bounces toward /signin)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": new Response(
          JSON.stringify({ error: { code: "unauthenticated", message: "x" } }),
          { status: 401 },
        ),
      }),
    );
    renderWithProviders(
      <RequireAuth>
        <div>Protected content</div>
      </RequireAuth>,
    );
    await waitFor(() => expect(screen.queryByText("Protected content")).not.toBeInTheDocument());
  });

  it("BUG-186: falls back to the protected me route during a frontend-first rolling deploy", async () => {
    const paths: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), "http://localhost").pathname;
        paths.push(path);
        if (path === "/auth/session") {
          return new Response(JSON.stringify({ error: { code: "not_found" } }), { status: 404 });
        }
        return new Response(
          JSON.stringify({ id: 1, email: "a@b.com", display_name: "A" }),
          { status: 200 },
        );
      }),
    );
    renderWithProviders(
      <RequireAuth>
        <div>Protected content</div>
      </RequireAuth>,
    );

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    expect(paths).toEqual(["/auth/session", "/auth/me"]);
  });

  it("BUG-010: a transient backend failure (5xx) shows a retry state, not the sign-in redirect a real 401 gets", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": new Response(
          JSON.stringify({ error: { code: "internal", message: "x" } }),
          { status: 500 },
        ),
      }),
    );
    renderWithProviders(
      <RequireAuth>
        <div>Protected content</div>
      </RequireAuth>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("could not reach the server");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });
});
