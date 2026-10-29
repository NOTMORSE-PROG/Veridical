import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { BulkResolveDialog, BulkSelectionBar } from "./BulkFindingReview";
import { ReviewContext, type ReviewScope } from "./criterionReviewContext";
import { clusterFlagFindings } from "./flagClusters";
import { SignalFlagsPanel } from "./SignalReviewSections";

function flag(overrides: Partial<FlagSummaryOut> = {}): FlagSummaryOut {
  return { id: 1, check_kind: "citation_integrity", severity: "med", criterion_text: null, evidence_excerpt: "Serquiña, 2025",
    page_anchor: "p. 45", overridden: false, is_passage_level: false, first_upload_context: false,
    confirmed_citation_source: false, problem_kind: "orphan_citation", affects_readiness: true, ...overrides };
}

const FLAGS = [flag(), flag({ id: 2, page_anchor: "p. 46", evidence_excerpt: "Cruz, 2020" }), flag({ id: 3, page_anchor: "p. 47", evidence_excerpt: "Reyes, 2019" })];
const REPORT = { check_run_id: 3, assessment_revision: 7 };

function respond(body: unknown, status = 200) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

function dialog(action: "confirm" | "dismiss", flags = FLAGS, viewed: number[] = []) {
  const onDone = vi.fn();
  renderWithProviders(<BulkResolveDialog action={action} clusters={clusterFlagFindings(flags)} checkRunId={3} expectedRevision={7}
    viewedIds={new Set(viewed)} onDone={onDone} onClose={vi.fn()} />);
  return onDone;
}

describe("bulk confirm and dismiss", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("dismisses only with a reason, says how many were not opened, and reports what was left unchanged", async () => {
    const calls = respond({ flags: [flag({ overridden: true }), flag({ id: 2, overridden: true })], unchanged_ids: [3], report: { ...REPORT, assessment_revision: 8 }, batch_id: "b1" });
    const onDone = dialog("dismiss", FLAGS, [1]);
    expect(screen.getByText("2 selected findings have not been opened yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a reason before dismissing.");
    expect(calls).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Reason for dismissing (required)"), { target: { value: "  Checked each entry against the list.  " } });
    expect(screen.getByText("36 of 2000 characters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("2 findings dismissed · 1 was already dismissed and was left unchanged."));
    expect(calls[0].url).toContain("/check-runs/3/flags/bulk-resolve");
    expect(calls[0].body).toEqual({ action: "dismiss", flag_ids: [1, 2, 3], expected_revision: 7, unopened_count: 2, reason: "Checked each entry against the list." });
  });

  it("confirms with an optional note, leaving out confirmed citation sources and saying dismissed ones reopen", async () => {
    const calls = respond({ flags: [flag({ finding_confirmed: true })], unchanged_ids: [], report: { ...REPORT, assessment_revision: 8 }, batch_id: "b2" });
    const onDone = dialog("confirm", [flag(), flag({ id: 2, page_anchor: "p. 46", evidence_excerpt: "Cruz, 2020", confirmed_citation_source: true, overridden: true }),
      flag({ id: 3, page_anchor: "p. 47", evidence_excerpt: "Reyes, 2019", overridden: true })]);
    expect(screen.getByRole("heading", { name: "Confirm 2 findings as real issues?" })).toBeInTheDocument();
    expect(screen.getByText(/1 finding is left out: a confirmed citation source/)).toBeInTheDocument();
    expect(screen.getByText(/1 selected finding was dismissed; confirming reopens it/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("1 finding confirmed."));
    expect(calls[0].body).toEqual({ action: "confirm", flag_ids: [1, 3], expected_revision: 7, unopened_count: 2, note: null });
  });

  it("shows a refusal as given and changes nothing on screen", async () => {
    respond({ error: { code: "conflict", message: "This assessment changed after you reviewed it. Nothing was changed." } }, 409);
    const onDone = dialog("confirm");
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText("This assessment changed after you reviewed it. Nothing was changed.")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("will not act on more locations than one action takes, or on a decided report", () => {
    const { rerender } = renderWithProviders(<BulkSelectionBar findings={3} locations={201} decided={false} onAction={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("201 locations are selected. One action takes at most 200");
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled();
    rerender(<BulkSelectionBar findings={3} locations={3} decided onAction={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/Reopen it to change findings/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm as real issues" })).toBeDisabled();
  });

  it("selects findings one by one or all in the check from the report's findings", async () => {
    respond(FLAGS);
    const review = { checkRunId: 3, decision: null, assessment: { ...REPORT, integrity_modules: [] } } as unknown as ReviewScope;
    renderWithProviders(<ReviewContext.Provider value={review}><SignalFlagsPanel checkRunId={3} unresolvedHighFlagCount={0} /></ReviewContext.Provider>);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Select finding: .*p\. 45/ }));
    expect(screen.getByText("1 finding selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select all in this check" }));
    expect(screen.getByText("3 findings selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
  });
});
