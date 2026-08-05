import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DashboardPage } from "./Dashboard";

const ACTIVE_FAMILY = [
  {
    id: 9,
    rubric_family_id: "fam-1",
    version: 2,
    title: "TIP Format",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    criteria_count: 24,
    report_count: 1,
  },
];

const STATS = {
  manuscripts_checked: 3,
  ready_count: 1,
  conditionally_ready_count: 1,
  not_ready_count: 1,
  needs_review_count: 0,
  escalations_awaiting_review: 2,
  escalation_rate: 0.5,
  escalation_budget: 0.2,
  system_underperforming: true,
};

const MANUSCRIPTS_PAGE = {
  items: [
    {
      id: 1,
      group_label: "G-11",
      ingest_status: "done",
      ingest_failure_reason: null,
      created_at: "2026-01-01T00:00:00Z",
      latest_check_run_id: 7,
      latest_check_run_status: "done",
      latest_done_check_run_id: 7,
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
};

describe("DashboardPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the first-run empty state (screen 4b) with the 3-step guide", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": [],
      }),
    );
    renderWithProviders(<DashboardPage />);
    // Staged reveal: nothing paints until `families` resolves (avoids a
    // flash of the wrong screen), so this is asynchronous now.
    expect(await screen.findByText("No required format yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload required format" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New check" })).toBeDisabled();
    await waitFor(() => expect(screen.getByText("Demo Instructor")).toBeInTheDocument());
  });

  it("renders the populated dashboard (screen 4e) once a rubric is active", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": { id: 1, email: "a@b.com", display_name: "Demo Instructor" },
        "/rubric-families": ACTIVE_FAMILY,
        "/stats": STATS,
        "/manuscripts": MANUSCRIPTS_PAGE,
      }),
    );
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText("3 manuscripts checked, by readiness status")).toBeInTheDocument();
    expect(screen.queryByText("No required format yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New check" })).toBeEnabled();
    expect((await screen.findAllByText("G-11")).length).toBeGreaterThan(0);
    expect(screen.getByText("System underperforming.")).toBeInTheDocument();
  });
});
