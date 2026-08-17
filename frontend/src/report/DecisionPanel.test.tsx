import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { DecisionPanel } from "./DecisionPanel";

const BASE_REPORT: ReportOut = {
  check_run_id: 5,
  manuscript_group_label: "Ungrouped",
  manuscript_original_filename: null,
  rubric_title: "TIP Format",
  status: "ready",
  composite_score: 92,
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

describe("DecisionPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the three peer decision buttons, all enabled, when nothing is pending review", () => {
    renderWithProviders(<DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />);
    for (const label of ["Approve for defense", "Return for revision", "Reject"]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
  });

  it("AC1: blocks the three buttons with a count and a working link to the escalated panel", () => {
    const report: ReportOut = { ...BASE_REPORT, pending_review_count: 2 };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);

    expect(screen.getByText(/2 criteria still need your review/)).toBeInTheDocument();
    for (const label of ["Approve for defense", "Return for revision", "Reject"]) {
      expect(screen.getByRole("button", { name: label })).toBeDisabled();
    }
    expect(screen.getByRole("link", { name: "Needs your review" })).toHaveAttribute(
      "href",
      "#escalated-heading",
    );
  });

  it("singular wording for exactly one pending item", () => {
    const report: ReportOut = { ...BASE_REPORT, pending_review_count: 1 };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);
    expect(screen.getByText(/1 criterion still needs your review/)).toBeInTheDocument();
  });

  it("opens the confirm modal for the specific decision clicked, not a chooser", () => {
    renderWithProviders(<DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />);
    fireEvent.click(screen.getByRole("button", { name: "Return for revision" }));
    expect(screen.getByRole("heading", { name: "Return for revision?" })).toBeInTheDocument();
  });

  it("submits the decision with the trimmed note, and empty note as null", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ ...BASE_REPORT, decision: "approved" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />);

    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve for defense" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/check-runs/5/decision");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "approved", note: null });
  });

  it("ux-critic finding: the note field's live character counter never corrupts the textarea's accessible name", () => {
    // The counter (and hint) must be OUTSIDE the <label>, not nested
    // inside it -- a nested counter folds "0 / 1000" into the field's
    // own accessible name and changes it on every keystroke, exactly the
    // bug class ReopenModal's own reason field was already built to
    // avoid. getByLabelText only succeeds via a stable name.
    renderWithProviders(<DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));
    const textarea = screen.getByLabelText("Note (optional)");
    fireEvent.change(textarea, { target: { value: "Looks solid." } });
    expect(screen.getByLabelText("Note (optional)")).toBe(textarea);
    expect(screen.getByText("12 / 1000")).toBeInTheDocument();
  });

  it("ux-critic finding: restates the report's own verdict in the confirm modal, not just the page header", () => {
    const report: ReportOut = { ...BASE_REPORT, status: "conditionally_ready", composite_score: 72 };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve for defense" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Conditionally Ready")).toBeInTheDocument();
    expect(within(dialog).getByText("72%")).toBeInTheDocument();
  });

  it("renders the decided state with badge, date, and note, and offers Reopen instead of the three buttons", () => {
    const report: ReportOut = {
      ...BASE_REPORT,
      decision: "rejected",
      decided_at: "2026-08-13T10:00:00Z",
      decision_note: "Missing a control group in Chapter 3.",
    };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);

    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText(/Missing a control group/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reopen this decision" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve for defense" })).not.toBeInTheDocument();
  });

  it("reopen requires a reason and posts it, with the prior decision named in the modal body", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ ...BASE_REPORT, decision: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const report: ReportOut = { ...BASE_REPORT, decision: "approved", decided_at: "2026-08-13T10:00:00Z" };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);

    fireEvent.click(screen.getByRole("button", { name: "Reopen this decision" }));
    expect(screen.getByText(/currently marked Approved for defense/)).toBeInTheDocument();

    // Blank reason blocked before it ever reaches the network.
    fireEvent.click(screen.getByRole("button", { name: "Reopen report" }));
    expect(screen.getByText("Enter a reason before reopening this report.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Reason (required)"), {
      target: { value: "Adviser asked for a second look." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reopen report" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/check-runs/5/reopen");
    expect(JSON.parse(init.body as string)).toEqual({ reason: "Adviser asked for a second look." });
  });

  it("shows the honest superseded-rubric warning without inventing version numbers", () => {
    const report: ReportOut = { ...BASE_REPORT, rubric_is_current: false };
    renderWithProviders(<DecisionPanel report={report} manuscriptLabel="G-11" />);
    const warning = screen.getByText(/A newer version of this rubric is now active/);
    expect(warning.textContent).not.toMatch(/v1|v2|version [0-9]/i);
  });

  it("renders the 409 error message verbatim when the state changed since page load", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/check-runs/5/decision": new Response(
          JSON.stringify({
            error: {
              code: "conflict",
              message: "This report has already been decided. Reopen it first to change the decision.",
            },
          }),
          { status: 409 },
        ),
      }),
    );
    renderWithProviders(<DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This report has already been decided. Reopen it first to change the decision.",
    );
  });

  it("moves focus to the section heading on a genuine decision-state transition", () => {
    // A real prop update on the SAME mounted instance, not a remount --
    // renderWithProviders' own `rerender` would replace the whole
    // QueryClientProvider/RouterProvider tree structurally (a different
    // root element type), which unmounts and re-mounts DecisionPanel
    // fresh rather than updating it, defeating the very transition this
    // test exists to prove. DecisionPanel itself uses no router APIs, so
    // a plain QueryClientProvider wrapper (needed only because its child
    // modals call useMutation) is enough here.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <DecisionPanel report={BASE_REPORT} manuscriptLabel="G-11" />
      </QueryClientProvider>,
    );
    const decided: ReportOut = { ...BASE_REPORT, decision: "approved", decided_at: "2026-08-13T10:00:00Z" };
    rerender(
      <QueryClientProvider client={queryClient}>
        <DecisionPanel report={decided} manuscriptLabel="G-11" />
      </QueryClientProvider>,
    );
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Final decision" }));
  });
});
