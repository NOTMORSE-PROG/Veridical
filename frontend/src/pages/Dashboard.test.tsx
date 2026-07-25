import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DashboardPage } from "./Dashboard";

describe("DashboardPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the first-run empty state (screen 4b) with the 3-step guide", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" } }),
    );
    renderWithProviders(<DashboardPage />);
    expect(screen.getByText("No required format yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload required format" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New check" })).toBeDisabled();
    await waitFor(() => expect(screen.getByText("Demo Instructor")).toBeInTheDocument());
  });
});
