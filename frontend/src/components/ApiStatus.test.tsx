import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiStatus } from "./ApiStatus";

describe("ApiStatus", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("reports unconfigured when no API base URL is set (local dev default)", () => {
    vi.stubEnv("VITE_API_BASE_URL", "");
    render(<ApiStatus />);
    expect(screen.getByText("API base URL not configured")).toBeInTheDocument();
  });

  it("reports ok status from a reachable /health endpoint", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: () => Promise.resolve({ status: "ok", db: "ok" }),
      }),
    );
    render(<ApiStatus />);
    await waitFor(() => expect(screen.getByText("API ok · db ok")).toBeInTheDocument());
  });

  it("reports unreachable when the fetch fails (CORS block, DNS, cold-start timeout)", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")));
    render(<ApiStatus />);
    await waitFor(() => expect(screen.getByText("API unreachable")).toBeInTheDocument());
  });
});
