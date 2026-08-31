import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  manuscript_group_label: "G-11",
  manuscript_uploaded_at: "2025-12-31T00:00:00Z",
  rubric_title: "Format",
};

const QUEUED = {
  ...RUNNING,
  status: "queued",
  stage_status: { stages: {} },
  queue_position: 3,
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

const DEGRADED = {
  ...RUNNING,
  status: "done",
  finished_at: "2026-01-01T01:00:00Z",
  stage_status: {
    stages: {
      ingesting: { status: "done" },
      structural: { status: "done" },
      semantic: { status: "done", n_criteria: 12, degraded_count: 3, degraded_code: "quota_exhausted" },
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

// BUG-032: an unexpected_error must never surface the raw exception string
// (which can contain a file path, or worse — see backend/app/db.py's
// hide_parameters note) to the instructor-facing banner.
const FAILED_UNEXPECTED = {
  ...RUNNING,
  status: "failed",
  finished_at: "2026-01-01T01:00:00Z",
  stage_status: {
    stages: { ingesting: { status: "done" }, structural: {} },
    failed: {
      code: "unexpected_error",
      message: "[Errno 2] No such file or directory: 'data/4.extraction.json'",
    },
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
    expect(screen.getByText("AI grading").closest("li")).toHaveClass("signal-check-stage--running");
    expect(screen.getByText("Integrity checks")).toBeInTheDocument();
    expect(screen.getByText("Readiness report")).toBeInTheDocument();
    expect(screen.getByText(/G-11, uploaded/)).toBeInTheDocument();
  });

  it("BUG-022: shows the manuscript's original filename, not just its (possibly default) group_label", async () => {
    const run = {
      ...RUNNING,
      manuscript_group_label: "Ungrouped",
      manuscript_original_filename: "Chapter1-3_FinalDefense.pdf",
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": run }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    expect(await screen.findByText(/Chapter1-3_FinalDefense\.pdf, uploaded/)).toBeInTheDocument();
    expect(screen.queryByText(/^Ungrouped,/)).not.toBeInTheDocument();
  });

  it("shows the queue position pill only while genuinely queued, not once running (locked-in bug fix)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": QUEUED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const queued = (await screen.findByText("Waiting to start")).closest("[role='status']");
    expect(queued).not.toBeNull();
    expect(queued).toHaveTextContent("Waiting to start");
    expect(queued).toHaveTextContent("Queue position 3");
  });

  it("hides the queue position pill once a run is actively running, even if queue_position is still non-null", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5": { ...RUNNING, queue_position: 1 } }),
    );
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByText("AI grading");
    expect(screen.queryByText(/Queue position/)).not.toBeInTheDocument();
  });

  it("shows a distinct quota_exhausted caption with the resume time on the blocked stage", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": BLOCKED_ON_QUOTA }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    expect(await screen.findByText(/Daily AI quota reached/)).toBeInTheDocument();
    expect(screen.getByText(/Resumes at/)).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
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

  it("shows the generic unexpected_error banner, never the raw stored exception text (BUG-032)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": FAILED_UNEXPECTED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Something went wrong while running this check");
    expect(banner).not.toHaveTextContent("Errno");
    expect(banner).not.toHaveTextContent("extraction.json");
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

  it("requires confirmation before cancelling and records the request through the run endpoint", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ ...RUNNING, cancel_requested_at: "2026-01-01T00:05:00Z" }), { status: 200 });
      }
      return new Response(JSON.stringify({ ...RUNNING, cancel_requested_at: null }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });

    fireEvent.click(await screen.findByRole("button", { name: "Cancel check" }));
    expect(screen.getByRole("dialog", { name: "Cancel this check?" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    fireEvent.click(within(screen.getByRole("dialog", { name: "Cancel this check?" })).getByRole("button", { name: "Cancel check" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/check-runs/5/cancel"),
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByText("Cancellation requested")).toBeDisabled();
  });

  it("renders a done-but-skipped stage as visually and textually distinct from a done stage (never conflated as passed)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": DONE }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByRole("link", { name: "View readiness report" });
    expect(screen.getByText("Skipped")).toBeInTheDocument();
    // "Done" tags exist for the genuinely-completed stages, distinct text
    // from "Skipped" for the honestly-not-implemented integrity stage.
    expect(screen.getAllByText("Done").length).toBeGreaterThan(0);
  });

  it("surfaces a degraded (quota-exhausted-mid-grading) stage as Needs review with the exact counts, not a plain Done", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": DEGRADED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByRole("link", { name: "View readiness report" });
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(
      screen.getByText(/3 of 12 criteria were not graded by AI \(today's free AI capacity was reached\)\. 9 criteria were graded normally\./),
    ).toBeInTheDocument();
  });

  it("shows a real loading state, then a real fetch-error state with a working retry", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(RUNNING), { status: 200 });
      }),
    );
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load this check's progress");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("AI grading")).toBeInTheDocument());
  });

  it("stops auto-polling once a fetch settles into a persisting error, instead of retrying forever (regression: previously stuck on 'Loading' indefinitely, hammering the backend)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn(async () => new Response("Not Found", { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });

    await vi.waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const callsAtError = fetchMock.mock.calls.length;

    // Advance well past several would-be 2s poll intervals.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchMock.mock.calls.length).toBe(callsAtError);

    vi.useRealTimers();
  });
});
