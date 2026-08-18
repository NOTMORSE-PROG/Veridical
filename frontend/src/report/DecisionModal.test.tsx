import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DecisionModal } from "./DecisionModal";

function reportWithStatus(status: ReportOut["status"], score: number | null): ReportOut {
  return {
    check_run_id: 5,
    manuscript_group_label: "Ungrouped",
    manuscript_original_filename: null,
    rubric_title: "TIP Format",
    status,
    composite_score: score,
    thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
    reason: null,
    flag_deduction: 0,
    unresolved_high_flag_count: 0,
    decision: null,
    decided_at: null,
    decision_note: null,
    pending_review_count: 0,
    rubric_is_current: true,
    llm_mode: "real",
    rubric_needs_review: false,
    rubric_parse_issues: null,
    previous_status: null,
    previous_composite_score: null,
    results: [],
  };
}

describe("DecisionModal (BUG-095)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("labels the note optional and confirms with no note when approving a Ready report (agrees with the verdict)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(reportWithStatus("ready", 92)), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <DecisionModal
        decision="approved"
        report={reportWithStatus("ready", 92)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Note (optional)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ decision: "approved", note: null });
  });

  it("requires a reason and blocks confirmation when approving a Not Ready report (disagrees with the verdict)", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(reportWithStatus("not_ready", 0)), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <DecisionModal
        decision="approved"
        report={reportWithStatus("not_ready", 0)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Reason (required)")).toBeInTheDocument();
    const confirmButton = screen.getByRole("button", { name: "Approve for defense" });
    expect(confirmButton).not.toBeDisabled(); // validated on click, never pre-disabled

    fireEvent.click(confirmButton);
    expect(await screen.findByText("Enter a reason before confirming.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts the decision once a reason is entered", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(reportWithStatus("not_ready", 0)), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onClose = vi.fn();
    renderWithProviders(
      <DecisionModal
        decision="approved"
        report={reportWithStatus("not_ready", 0)}
        manuscriptLabel="G-11"
        onClose={onClose}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason (required)"), {
      target: { value: "Panel already reviewed the flagged sections in person." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      decision: "approved",
      note: "Panel already reviewed the flagged sections in person.",
    });
  });

  it("BUG-095 follow-up: a short reason (\"ok\") is rejected, not just presence-checked", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(reportWithStatus("not_ready", 0)), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <DecisionModal
        decision="approved"
        report={reportWithStatus("not_ready", 0)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "ok" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));

    expect(await screen.findByText(/Reason must be at least 10 characters/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a reason when approving or rejecting a needs_review report (nothing was computed at all)", () => {
    vi.stubGlobal("fetch", stubFetchByPath({}));
    const { unmount } = renderWithProviders(
      <DecisionModal
        decision="approved"
        report={reportWithStatus("needs_review", null)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Reason (required)")).toBeInTheDocument();
    unmount();

    renderWithProviders(
      <DecisionModal
        decision="rejected"
        report={reportWithStatus("needs_review", null)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Reason (required)")).toBeInTheDocument();
  });

  it("requires a reason when rejecting a Ready report", () => {
    vi.stubGlobal("fetch", stubFetchByPath({}));
    renderWithProviders(
      <DecisionModal
        decision="rejected"
        report={reportWithStatus("ready", 92)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Reason (required)")).toBeInTheDocument();
  });

  it("never requires a reason for Return, regardless of status", () => {
    vi.stubGlobal("fetch", stubFetchByPath({}));
    renderWithProviders(
      <DecisionModal
        decision="returned"
        report={reportWithStatus("not_ready", 0)}
        manuscriptLabel="G-11"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Note (optional)")).toBeInTheDocument();
  });
});
