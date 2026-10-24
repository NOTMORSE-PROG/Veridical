import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

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

  it("explains waiting without presenting a legacy numeric position as execution order", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": QUEUED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const queued = (await screen.findByText("Waiting to start")).closest(".signal-alert");
    expect(queued).not.toBeNull();
    expect(queued).toHaveTextContent("Waiting to start");
    expect(queued).toHaveTextContent("start order can change");
    expect(queued).toHaveTextContent("A start time is not available");
    expect(screen.queryByText(/Queue position/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Check #5. Waiting to start. Start order can change across instructors.");
    expect(screen.getByRole("status")).toHaveAttribute("aria-atomic", "true");
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
    expect(await screen.findByText(/AI capacity was reached\. This check is paused\. Eligible to retry after/)).toBeInTheDocument();
    expect(screen.queryByText(/Resumes at|resume automatically/)).not.toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("shows a distinct file_malformed failure banner (never conflated with api_down)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": FAILED }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const banner = (await screen.findByText("This check stopped")).closest(".signal-alert");
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
    const banner = (await screen.findByText("This check stopped")).closest(".signal-alert");
    expect(banner).toHaveTextContent("Something went wrong while running this check");
    expect(banner).not.toHaveTextContent("Errno");
    expect(banner).not.toHaveTextContent("extraction.json");
  });

  it("opens the manuscript review directly once the check is done", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": DONE }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    const link = await screen.findByRole("link", { name: "Open manuscript review" });
    expect(link).toHaveAttribute("href", "/report/5/document");
    expect(screen.getByRole("status")).toHaveTextContent("Check finished. Open manuscript review to check the results and assessment limits.");
  });

  it("does not show the report link for a still-running check", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": RUNNING }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByText("AI grading");
    expect(screen.queryByRole("link", { name: "Open manuscript review" })).not.toBeInTheDocument();
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
    expect(await screen.findByRole("button", { name: "Cancellation requested" })).toBeDisabled();
  });

  it("renders a done-but-skipped stage as visually and textually distinct from a done stage (never conflated as passed)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": DONE }));
    renderWithProviders(<CheckProgressPage />, {
      route: "/checks/5",
      path: "/checks/:checkRunId",
    });
    await screen.findByRole("link", { name: "Open manuscript review" });
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
    await screen.findByRole("link", { name: "Open manuscript review" });
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(
      screen.getByText(/3 of 12 criteria were not graded by AI \(today's free AI capacity was reached\)/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/graded normally/)).not.toBeInTheDocument();
    expect(screen.getByText(/after the daily reset/)).toBeInTheDocument();
  });

  it.each(["done", "integrity", "failed", "cancelled"])("keeps outage advice stage-local when the run is %s", async (status) => {
    const run = {
      ...DEGRADED, status,
      stage_status: { ...DEGRADED.stage_status, stages: {
        ...DEGRADED.stage_status.stages,
        semantic: { status: "done", n_criteria: 12, degraded_count: 3, degraded_code: "api_down" },
      } },
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": run }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const detail = await screen.findByText(/3 of 12 semantic criteria have no completed AI grade because an AI service was unavailable/);
    expect(detail).toHaveTextContent("AI grading of these ungraded semantic criteria will not retry automatically in this run. Other checks may continue.");
    expect(detail).toHaveTextContent("If a report is produced, review the ungraded criteria in manuscript review and record your own judgments. You can also start a new check later.");
    expect(detail).not.toHaveTextContent(/daily reset|graded normally|This completed run/);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    if (status !== "done") expect(screen.queryByRole("link", { name: "Open manuscript review" })).not.toBeInTheDocument();
  });

  it("does not promise a quota reset for an unknown degraded cause", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": {
      ...DEGRADED, stage_status: { stages: { semantic: {
        status: "done", n_criteria: 12, degraded_count: 3, degraded_code: "new_service_cause",
      } } },
    } }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const detail = await screen.findByText(/3 of 12 criteria were not graded by AI/);
    expect(detail).not.toHaveTextContent(/daily reset|graded normally/);
  });

  it("does not invent an outage warning when no criterion was degraded", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": {
      ...DEGRADED, stage_status: { stages: { semantic: {
        status: "done", n_criteria: 12, degraded_count: 0, degraded_code: "api_down",
      } } },
    } }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    await screen.findByRole("link", { name: "Open manuscript review" });
    expect(screen.queryByText("Needs review")).not.toBeInTheDocument();
    expect(screen.queryByText(/service was unavailable/)).not.toBeInTheDocument();
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

  it.each([null, "2020-01-01T00:00:00Z"])("does not promise resumption for retry eligibility %s", async (resumeAt) => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": {
      ...RUNNING,
      stage_status: { blocked: { code: "api_down", message: "offline", resume_at: resumeAt } },
    } }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    await screen.findByText("Paused: service unavailable");
    expect(screen.getByRole("status")).toHaveTextContent(resumeAt ? "Eligible to retry after" : "No retry time is available");
    expect(screen.queryByText(/will resume|Resumes at|Starting now/)).not.toBeInTheDocument();
    expect(screen.queryByText("Current stage")).not.toBeInTheDocument();
  });

  it("announces same-stage blocks and cancellation, while identical polls leave the status node unchanged", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let response: unknown = RUNNING;
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(response)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const region = await screen.findByRole("status");
    await waitFor(() => expect(region).toHaveTextContent("Current stage: AI grading"));
    const updates: MutationRecord[] = [];
    const observer = new MutationObserver((records) => updates.push(...records));
    observer.observe(region, { subtree: true, childList: true, characterData: true });
    const poll = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2_000); }); };
    try {
      await poll();
      expect(updates).toHaveLength(0);
      response = BLOCKED_ON_QUOTA;
      await poll();
      expect(region).toHaveTextContent("Check paused because AI capacity was reached");
      expect(updates.length).toBeGreaterThan(0);
      updates.length = 0;
      await poll();
      expect(updates).toHaveLength(0);
      response = { ...BLOCKED_ON_QUOTA, stage_status: { blocked: { ...BLOCKED_ON_QUOTA.stage_status.blocked, resume_at: null } } };
      await poll();
      expect(region).toHaveTextContent("No retry time is available");
      response = { ...BLOCKED_ON_QUOTA, cancel_requested_at: "2026-01-01T00:05:00Z" };
      await poll();
      expect(region).toHaveTextContent("Cancellation requested. Waiting for the check to stop");
      expect(region).not.toHaveTextContent("Check paused");
      expect(screen.queryByText("Paused")).not.toBeInTheDocument();
      expect(screen.queryByText("Waiting to start")).not.toBeInTheDocument();
    } finally { observer.disconnect(); }
  });

  it("keeps terminal degraded results distinct even with stale block and cancellation metadata", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": {
      ...DEGRADED,
      cancel_requested_at: "2026-01-01T00:05:00Z",
      stage_status: { ...DEGRADED.stage_status, blocked: BLOCKED_ON_QUOTA.stage_status.blocked },
    } }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    await screen.findByRole("link", { name: "Open manuscript review" });
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Check finished");
    expect(screen.queryByText("Cancellation requested")).not.toBeInTheDocument();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
  });

  it("clears the prior stage announcement when a subsequent progress fetch fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fail = false;
    vi.stubGlobal("fetch", vi.fn(async () => fail ? new Response("offline", { status: 500 }) : Response.json(RUNNING)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const region = await screen.findByRole("status");
    await waitFor(() => expect(region).toHaveTextContent("Current stage"));
    fail = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this check's progress");
    expect(region).toBeEmptyDOMElement();
  });

  it.each(["semantic", "cancelled"])("continues at the persistent heading after cancellation acknowledges %s", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => Response.json(init?.method === "POST"
      ? { ...RUNNING, status, cancel_requested_at: "2026-01-01T00:05:00Z" }
      : RUNNING)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    const confirm = within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel check" });
    confirm.focus(); fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Check progress", level: 1 })).toHaveFocus();
    if (status === "semantic") expect(trigger).toBeDisabled();
    else expect(trigger).not.toBeInTheDocument();
  });

  it.each(["Escape", "Close"])("pending %s dismissal has one continuation and respects focus after late acknowledgment", async (dismiss) => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => init?.method === "POST" ? pending : Response.json(RUNNING)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Cancel check" });
    confirm.focus(); fireEvent.click(confirm);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Keep checking" })).toBeDisabled());
    if (dismiss === "Escape") fireEvent.keyDown(document, { key: "Escape" });
    else fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Check progress", level: 1 })).toHaveFocus();
    const back = screen.getByRole("link", { name: "Back to Review Desk" });
    back.focus();
    await act(async () => finish(Response.json({ ...RUNNING, status: "cancelled", cancel_requested_at: "2026-01-01T00:05:00Z" })));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Check cancelled"));
    expect(back).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["Keep checking", "Close", "Escape"])("restores the enabled trigger for pre-submit %s", async (dismiss) => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": RUNNING }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    if (dismiss === "Escape") fireEvent.keyDown(document, { key: "Escape" });
    else fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: dismiss }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("hands focus to the enabled Close button before submitting and keeps it through failure", async () => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    let focusAtPost: Element | null = null;
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => {
      if (init?.method === "POST") { focusAtPost = document.activeElement; return pending; }
      return Response.json(RUNNING);
    }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close" });
    const confirm = within(dialog).getByRole("button", { name: "Cancel check" });
    confirm.focus(); fireEvent.click(confirm);
    await waitFor(() => expect(confirm).toBeDisabled());
    expect(focusAtPost).toBe(close);
    expect(close).toHaveFocus();
    await act(async () => finish(Response.json({ error: { code: "api_down", message: "Control service unavailable" } }, { status: 503 })));
    await within(dialog).findByRole("alert");
    expect(close).toHaveFocus();
    expect(confirm).toBeEnabled();
  });

  it("does not take an existing outside focus destination when a stale confirmation submits", async () => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => init?.method === "POST" ? pending : Response.json(RUNNING)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Cancel check" });
    const outside = document.createElement("button");
    document.body.append(outside);
    try {
      outside.focus(); fireEvent.click(confirm);
      await waitFor(() => expect(confirm).toBeDisabled());
      expect(outside).toHaveFocus();
      await act(async () => finish(Response.json({ error: { code: "api_down", message: "Control service unavailable" } }, { status: 503 })));
      await within(dialog).findByRole("alert");
      expect(outside).toHaveFocus();
    } finally { outside.remove(); }
  });

  it("keeps a failed cancellation open and restores its enabled trigger after dismissal", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input, init) => init?.method === "POST"
      ? Response.json({ error: { code: "api_down", message: "Control service unavailable" } }, { status: 503 })
      : Response.json(RUNNING)));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    const trigger = await screen.findByRole("button", { name: "Cancel check" });
    trigger.focus(); fireEvent.click(trigger);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel check" }));
    await within(screen.getByRole("dialog")).findByRole("alert");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Keep checking" }));
    expect(trigger).toHaveFocus();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    fireEvent.click(trigger);
    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("Could not cancel this check");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
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


describe("AI configuration progress", () => {
  afterEach(() => vi.unstubAllGlobals());
  it.each([
    ["ai_not_configured", "no usable AI configuration was available for this check"],
    ["ai_key_rejected", "no applicable alternative completed that request"],
  ])("keeps %s distinct and provides conditional manual recovery", async (code, cause) => {
    const run = { ...DONE, stage_status: { stages: { semantic: {
      status: "done", n_criteria: 5, degraded_count: 2, degraded_code: code,
    } } } };
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5": run }));
    renderWithProviders(<CheckProgressPage />, { route: "/checks/5", path: "/checks/:checkRunId" });
    await screen.findByRole("link", { name: "Open manuscript review" });
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    const detail = screen.getByText(new RegExp(cause));
    expect(detail).toHaveTextContent("2 of 5 semantic criteria have no completed AI grade");
    expect(detail).toHaveTextContent("If a report is produced");
    expect(detail).toHaveTextContent("will not retry automatically in this run");
    expect(detail).not.toHaveTextContent("daily reset");
  });
});
