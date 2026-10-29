import { useQueryClient } from "@tanstack/react-query";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReportOut } from "../api/types";
import { renderWithReviewProviders as renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalDecisionPanel } from "./SignalDecisionPanel";

function QuerySeed({ report }: { report: ReportOut }) {
  const client = useQueryClient();
  client.setQueryData(["report", report.check_run_id], report);
  return null;
}

const BASE_REPORT: ReportOut = {
  assessment_revision: 0,
  check_run_id: 42,
  manuscript_id: 10,
  manuscript_group_label: "Group Adversarial Challenge",
  manuscript_original_filename: "challenge_manuscript.pdf",
  rubric_title: "Defense Format",
  status: "needs_review",
  composite_score: null,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: "Unresolved criteria require instructor review.",
  flag_deduction: 0,
  unresolved_high_flag_count: 0,
  decision: null,
  decided_at: null,
  decision_note: null,
  pending_review_count: 2,
  rubric_is_current: true,
  llm_mode: "real",
  rubric_needs_review: false,
  rubric_parse_issues: null,
  previous_status: null,
  previous_composite_score: null,
  integrity_check_status: [],
  results: [
    {
      criterion_id: 1,
      check_result_id: 101,
      text: "Unresolved Methodology Criterion",
      type: "semantic",
      weight: 50,
      weight_importance: "high",
      kind: "semantic",
      outcome: "escalated",
      score: null,
      basis: "llm",
      anchor: null,
      reasoning: "Review needed",
      reason: "Split decision between evaluators",
      evidence: [],
      resolution: null,
    },
    {
      criterion_id: 2,
      check_result_id: 102,
      text: "Unresolved Analysis Criterion",
      type: "semantic",
      weight: 50,
      weight_importance: "high",
      kind: "semantic",
      outcome: "escalated",
      score: null,
      basis: "llm",
      anchor: null,
      reasoning: "Review needed",
      reason: "Split decision between evaluators",
      evidence: [],
      resolution: null,
    },
  ],
};

describe("Adversarial Challenge: Return for Revision & Unresolved Criteria Preservation", () => {
  it("opens the mounted criteria step from the decision recovery link and preserves return context", () => {
    renderWithProviders(<SignalDecisionPanel report={BASE_REPORT} manuscriptLabel="Control" />, {
      route: "/report/5?review_step=decision&desk_return=%2Fdashboard&module=citation_integrity",
      path: "/report/:checkRunId",
    });
    const link = screen.getByRole("link", { name: "Review unresolved criteria." });
    const target = new URL(link.getAttribute("href")!, "https://example.test");
    expect(target.searchParams.get("review_step")).toBe("criteria");
    expect(target.searchParams.get("desk_return")).toBe("/dashboard");
    expect(target.searchParams.get("module")).toBe("citation_integrity");
    expect(target.hash).toBe("#review-criteria");
  });

  it("stays on the current screen when the host already shows the unresolved criteria", () => {
    const onReviewCriteria = vi.fn();
    renderWithProviders(<SignalDecisionPanel report={BASE_REPORT} manuscriptLabel="Control" onReviewCriteria={onReviewCriteria} />, {
      route: "/report/5/document?view=decision&pane=review",
      path: "/report/:checkRunId/document",
    });
    expect(screen.queryByRole("link", { name: /Review unresolved criteria/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review unresolved criteria" }));
    expect(onReviewCriteria).toHaveBeenCalledOnce();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("blocks approval and rejection while preserving return for revision when criteria are unresolved", async () => {
    stubFetchByPath({
      "/check-runs/42/report": BASE_REPORT,
    });

    renderWithProviders(
      <SignalDecisionPanel
        report={BASE_REPORT}
        manuscriptLabel={BASE_REPORT.manuscript_group_label}
      />,
      { route: "/report/42", path: "/report/:checkRunId" },
    );

    expect(screen.getByRole("button", { name: "Approve for defense" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject manuscript" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Return for revision" })).toBeEnabled();

    expect(screen.getByText(/2 criteria need your judgment/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Returning does not grade or exclude these criteria/i),
    ).toBeInTheDocument();
  });

  it("enforces minimum reason length and preserves unresolved criteria upon return", async () => {
    let decidePayload: any = null;
    const fetch = stubFetchByPath({
      "/check-runs/42/report": BASE_REPORT,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/decision") && init?.method === "POST") {
        decidePayload = JSON.parse(String(init.body));
        const returnedReport: ReportOut = {
          ...BASE_REPORT,
          decision: "returned",
          decided_at: new Date().toISOString(),
          decision_note: decidePayload.note,
          assessment_revision: 1,
          pending_review_count: 2, // UNRESOLVED PRESERVED!
        };
        return new Response(JSON.stringify(returnedReport), { status: 200 });
      }
      return fetch(input, init);
    }));

    renderWithProviders(
      <>
        <QuerySeed report={BASE_REPORT} />
        <SignalDecisionPanel
          report={BASE_REPORT}
          manuscriptLabel={BASE_REPORT.manuscript_group_label}
        />
      </>,
      { route: "/report/42", path: "/report/:checkRunId" },
    );

    // Open Return for revision dialog
    fireEvent.click(screen.getByRole("button", { name: "Return for revision" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /with 2 unresolved criteria\. Returning does not grade or exclude them\./i,
      ),
    ).toBeInTheDocument();

    // Wait for the query to settle so canSubmit is true
    await waitFor(() => {
      expect(
        within(dialog).queryByText(/The assessment changed or needs a current read/i),
      ).not.toBeInTheDocument();
    });

    // Confirm button inside dialog is now enabled
    const confirmBtn = within(dialog).getByRole("button", { name: "Return for revision" });
    expect(confirmBtn).toBeEnabled();

    // Attempting to submit without reason or with short reason (<10 chars)
    fireEvent.click(confirmBtn);
    expect(
      within(dialog).getByText(/Enter a reason for returning the manuscript with unresolved criteria\./i),
    ).toBeInTheDocument();
    expect(decidePayload).toBeNull();

    const textarea = within(dialog).getByLabelText(/Reason for return \(required\)/i);
    fireEvent.change(textarea, { target: { value: "Short" } });
    fireEvent.click(confirmBtn);
    expect(
      within(dialog).getByText(/Enter at least 10 characters and explain your judgment\./i),
    ).toBeInTheDocument();
    expect(decidePayload).toBeNull();

    // Now enter valid reason >= 10 characters
    fireEvent.change(textarea, {
      target: { value: "Please address Chapter 3 methodology and revise analysis section." },
    });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(decidePayload).toEqual({
        decision: "returned",
        note: "Please address Chapter 3 methodology and revise analysis section.",
        expected_revision: 0,
      });
    });
  });

  it("locks judgment changes and shows unresolved count when report is in returned state", async () => {
    const returnedReport: ReportOut = {
      ...BASE_REPORT,
      decision: "returned",
      decided_at: new Date().toISOString(),
      decision_note: "Returned due to pending criteria and need for revision.",
      assessment_revision: 1,
      pending_review_count: 2,
    };

    renderWithProviders(
      <SignalDecisionPanel
        report={returnedReport}
        manuscriptLabel={returnedReport.manuscript_group_label}
      />,
      { route: "/report/42", path: "/report/:checkRunId" },
    );

    // Verify unresolved preservation message is displayed
    expect(
      screen.getByText(
        /2 criteria remain unresolved\. Their original outcomes and assessment limits are preserved\. Reopen this decision before making further judgments\./i,
      ),
    ).toBeInTheDocument();

    // Decisions cannot be modified without reopening
    expect(screen.queryByRole("button", { name: "Approve for defense" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject manuscript" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reopen this decision" })).toBeEnabled();
  });

  it("enforces reason on reopen and preserves all unresolved criteria after reopening", async () => {
    let reopenPayload: any = null;
    const returnedReport: ReportOut = {
      ...BASE_REPORT,
      decision: "returned",
      decided_at: new Date().toISOString(),
      decision_note: "Returned due to pending criteria and need for revision.",
      assessment_revision: 1,
      pending_review_count: 2,
    };

    const fetch = stubFetchByPath({
      "/check-runs/42/report": returnedReport,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reopen") && init?.method === "POST") {
        reopenPayload = JSON.parse(String(init.body));
        const reopenedReport: ReportOut = {
          ...returnedReport,
          decision: null,
          decided_at: null,
          decision_note: null,
          assessment_revision: 2,
          pending_review_count: 2, // UNRESOLVED STILL PRESERVED!
        };
        return new Response(JSON.stringify(reopenedReport), { status: 200 });
      }
      return fetch(input, init);
    }));

    renderWithProviders(
      <>
        <QuerySeed report={returnedReport} />
        <SignalDecisionPanel
          report={returnedReport}
          manuscriptLabel={returnedReport.manuscript_group_label}
        />
      </>,
      { route: "/report/42", path: "/report/:checkRunId" },
    );

    // Click Reopen this decision
    fireEvent.click(screen.getByRole("button", { name: "Reopen this decision" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /The current decision stays in Audit\. Reopening permits further instructor review\./i,
      ),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(
        within(dialog).queryByText(/The assessment changed or needs a current read/i),
      ).not.toBeInTheDocument();
    });

    const confirmBtn = within(dialog).getByRole("button", { name: "Reopen decision" });
    expect(confirmBtn).toBeEnabled();

    // Short reason < 10 chars rejected
    const textarea = within(dialog).getByLabelText(/Reason \(required\)/i);
    fireEvent.change(textarea, { target: { value: "Short" } });
    fireEvent.click(confirmBtn);
    expect(
      within(dialog).getByText(/Enter at least 10 characters\./i),
    ).toBeInTheDocument();
    expect(reopenPayload).toBeNull();

    // Valid reason >= 10 chars
    fireEvent.change(textarea, {
      target: { value: "Reopening to inspect student revision of Chapter 3." },
    });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(reopenPayload).toEqual({
        reason: "Reopening to inspect student revision of Chapter 3.",
        expected_revision: 1,
      });
    });
  });
});
