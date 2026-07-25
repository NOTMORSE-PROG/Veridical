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
});
