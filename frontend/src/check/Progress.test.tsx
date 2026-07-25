import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { CheckProgressPage } from "./Progress";

const RUNNING = {
  id: 5,
  manuscript_id: 1,
  rubric_id: 1,
  status: "semantic",
  stage_status: {
    stages: {
      ingesting: { status: "done" },
      structural: { status: "done" },
    },
  },
  queue_position: null,
  started_at: "2026-01-01T00:00:00Z",
  finished_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const BLOCKED_ON_QUOTA = {
  ...RUNNING,
  stage_status: {
    stages: { ingesting: { status: "done" }, structural: { status: "done" } },
    blocked: { code: "quota_exhausted", message: "quota gone", resume_at: "2026-01-02T08:00:00Z" },
  },
};

const DONE = {
  ...RUNNING,
  status: "done",
  finished_at: "2026-01-01T01:00:00Z",
  stage_status: {
    stages: {
      ingesting: { status: "done" },
      structural: { status: "done" },
      semantic: { status: "done" },
      integrity: { status: "skipped", note: "not implemented yet" },
      aggregating: { status: "done" },
    },
  },
};

const FAILED = {
  ...RUNNING,
  status: "failed",
  finished_at: "2026-01-01T01:00:00Z",
  stage_status: {
    stages: { ingesting: {} },
    failed: { code: "file_malformed", message: "bad file" },
  },
};

describe("CheckProgressPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the 5 V2 stages with the currently-running one highlighted", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": RUNNING }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByText("AI grading");
    expect(screen.getByText("Ingestion")).toBeInTheDocument();
    expect(screen.getByText("Structural checks")).toBeInTheDocument();
    expect(screen.getByText("AI grading")).toHaveClass("font-semibold");
    expect(screen.getByText("Integrity checks")).toBeInTheDocument();
    expect(screen.getByText("Readiness report")).toBeInTheDocument();
  });

  it("shows a queue position chip when set", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5": { ...RUNNING, queue_position: 3 } }),
    );
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    expect(await screen.findByText("Queue position 3")).toBeInTheDocument();
  });

  it("shows a distinct quota_exhausted banner with the resume time", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": BLOCKED_ON_QUOTA }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Daily AI quota reached");
    expect(banner).toHaveTextContent("Resumes at");
  });

  it("shows a distinct file_malformed failure banner (never conflated with api_down)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": FAILED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("failed ingestion");
    expect(banner).not.toHaveTextContent("quota");
    expect(banner).not.toHaveTextContent("unavailable");
  });

  it("shows a View readiness report link only once done", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": DONE }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const link = await screen.findByRole("link", { name: "View readiness report" });
    expect(link).toHaveAttribute("href", "/report/5");
  });

  it("does not show the report link for a still-running check", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": RUNNING }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByText("AI grading");
    expect(screen.queryByRole("link", { name: "View readiness report" })).not.toBeInTheDocument();
  });
});
