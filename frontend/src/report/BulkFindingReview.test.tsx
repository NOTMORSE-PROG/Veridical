import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState, type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
const REASON = "Checked each entry against the list.";

function respond(body: unknown, status = 200) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

function Harness(props: Omit<ComponentProps<typeof BulkResolveDialog>, "text" | "onText">) {
  const [text, setText] = useState("");
  return <BulkResolveDialog {...props} text={text} onText={setText} />;
}

function dialog(action: "confirm" | "dismiss", flags = FLAGS, viewed: number[] = []) {
  const onDone = vi.fn();
  renderWithProviders(<Harness action={action} clusters={clusterFlagFindings(flags)} checkRunId={3} expectedRevision={7}
    viewedIds={new Set(viewed)} onDone={onDone} onClose={vi.fn()} />);
  return onDone;
}

function panel(flags: FlagSummaryOut[]) {
  respond(flags);
  const review = { checkRunId: 3, decision: null, assessment: { ...REPORT, integrity_modules: [] } } as unknown as ReviewScope;
  return renderWithProviders(<ReviewContext.Provider value={review}><SignalFlagsPanel checkRunId={3} unresolvedHighFlagCount={0} /></ReviewContext.Provider>,
    { route: "/report/3?section=findings&module=citation_integrity" });
}

describe("bulk confirm and dismiss", () => {
  beforeEach(() => { window.sessionStorage.clear(); window.localStorage.clear(); });
  afterEach(() => vi.unstubAllGlobals());

  it("lists every selected finding with what it is about, whether it was opened, and its evidence", () => {
    respond({});
    dialog("dismiss", FLAGS, [1]);
    const list = screen.getByRole("list", { name: "Selected findings" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByText("Cruz, 2020")).toBeInTheDocument();
    expect(within(list).getAllByText("Not opened")).toHaveLength(2);
    expect(within(list).getAllByText("Opened")).toHaveLength(1);
    expect(within(list).getAllByText("Medium severity · counts for readiness")).toHaveLength(3);
    const link = within(list).getByRole("link", { name: /Review evidence: .*p\. 46, Cruz, 2020 \(opens in a new tab\)/ });
    expect(link).toHaveAttribute("href", "/flags/2");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("dismisses only with a real reason and an explicit word for unopened findings, then reports what was left unchanged", async () => {
    const calls = respond({ flags: [flag({ overridden: true }), flag({ id: 2, overridden: true })], unchanged_ids: [3], report: { ...REPORT, assessment_revision: 8 }, batch_id: "b1" });
    const onDone = dialog("dismiss", FLAGS, [1]);
    expect(screen.getByText("2 selected findings have not been fully opened here.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Reason for dismissing (required)"), { target: { value: "ok" } });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByText("Enter a reason of at least 10 characters before dismissing.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Reason for dismissing (required)"), { target: { value: `  ${REASON}  ` } });
    expect(screen.getByText("36 of 2000 characters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByText(/Confirm that you have checked the findings you have not opened here/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: /I have checked these findings, including the 2 I have not fully opened here/ }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("2 findings dismissed · 1 was already resolved and was left unchanged."));
    expect(calls[0].url).toContain("/check-runs/3/flags/bulk-resolve");
    expect(calls[0].body).toEqual({ action: "dismiss", flag_ids: [1, 2, 3], expected_revision: 7, unopened_count: 2, reason: REASON });
  });

  it("says a reason is too long while it is being typed", () => {
    respond({});
    dialog("dismiss", FLAGS, [1, 2, 3]);
    const field = screen.getByLabelText("Reason for dismissing (required)");
    fireEvent.change(field, { target: { value: "x".repeat(2005) } });
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Keep it to 2000 characters.");
  });

  it("confirms with an optional note, leaving out confirmed citation sources and saying dismissed ones reopen", async () => {
    const calls = respond({ flags: [flag({ finding_confirmed: true })], unchanged_ids: [], report: { ...REPORT, assessment_revision: 8 }, batch_id: "b2" });
    const onDone = dialog("confirm", [flag(), flag({ id: 2, page_anchor: "p. 46", evidence_excerpt: "Cruz, 2020", confirmed_citation_source: true, overridden: true }),
      flag({ id: 3, page_anchor: "p. 47", evidence_excerpt: "Reyes, 2019", affects_readiness: false, readiness_exclusion_reason: "unassessed_evidence" })], [1, 3]);
    expect(screen.getByRole("heading", { name: "Confirm 2 findings as real issues?" })).toBeInTheDocument();
    expect(screen.getByText(/1 finding is left out: a confirmed citation source/)).toBeInTheDocument();
    expect(screen.getByText("1 of these counts for readiness and stays counted; confirming the other 1 does not change readiness.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("1 finding confirmed."));
    expect(calls[0].body).toEqual({ action: "confirm", flag_ids: [1, 3], expected_revision: 7, unopened_count: 0, note: null });
  });

  it("guards confirming unopened findings as firmly as dismissing them", async () => {
    const calls = respond({ flags: [], unchanged_ids: [], report: { ...REPORT }, batch_id: null });
    dialog("confirm", FLAGS, [1]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText(/Confirm that you have checked the findings you have not opened here/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("shows a refused reason under the reason, and nowhere else", async () => {
    const sentence = "This text contains an invisible character that can't be saved. Retype it or paste it as plain text.";
    respond({ detail: [{ type: "text_not_storable", loc: ["body", "reason"], msg: sentence, input: "x" }] }, 422);
    const onDone = dialog("dismiss", FLAGS, [1, 2, 3]);
    const field = screen.getByLabelText("Reason for dismissing (required)");
    fireEvent.change(field, { target: { value: REASON } });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByText(`${sentence} Nothing was changed.`)).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(field).toHaveFocus());
    expect(screen.queryByRole("heading", { name: "Nothing was changed" })).not.toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: `${REASON} Again.` } });
    expect(field).not.toHaveAttribute("aria-invalid");
  });

  it.each([
    ["the selection", { type: "value_error", loc: ["body", "flag_ids"], msg: "Value error, select at most 200 findings at a time", input: [] }, "Select at most 200 findings at a time."],
    ["the request as a whole", { type: "value_error", loc: ["body"], msg: "Value error, a confirmation takes an optional note, not a reason", input: {} }, "A confirmation takes an optional note, not a reason."],
  ])("says a refusal of %s once for the dialog, never at the comment", async (_name, item, said) => {
    respond({ detail: [item] }, 422);
    dialog("confirm", FLAGS, [1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText(said)).toBeInTheDocument();
    expect(screen.getByLabelText("Comment (optional)")).not.toHaveAttribute("aria-invalid");
  });

  it("brings a dialog-level refusal into view and focuses it, so focus never escapes the dialog", async () => {
    respond({ detail: [{ type: "value_error", loc: ["body", "flag_ids"], msg: "Value error, select at most 200 findings at a time", input: [] }] }, 422);
    dialog("confirm", FLAGS, [1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("These findings were not changed")).toBeInTheDocument();
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("says both refusals when the comment and the selection are refused together", async () => {
    respond({ detail: [
      { type: "text_not_storable", loc: ["body", "note"], msg: "This text can't be saved.", input: "x" },
      { type: "value_error", loc: ["body", "flag_ids"], msg: "Value error, select at most 200 findings at a time", input: [] },
    ] }, 422);
    dialog("confirm", FLAGS, [1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText("This text can't be saved. Nothing was changed.")).toBeInTheDocument();
    expect(screen.getByText("Select at most 200 findings at a time.")).toBeInTheDocument();
    // The refused comment keeps focus; the selection refusal is still announced.
    await waitFor(() => expect(screen.getByLabelText("Comment (optional)")).toHaveFocus());
  });

  it("never blames the comment with the generic sentence when the request was refused", async () => {
    respond({ detail: [{ type: "missing", loc: ["body", "expected_revision"], msg: "Field required", input: {} }] }, 422);
    dialog("confirm", FLAGS, [1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText(/The selection may have changed/)).toBeInTheDocument();
    expect(screen.queryByText(/Check what you entered/)).not.toBeInTheDocument();
  });

  it.each([[500, { error: { code: "internal", message: "Something went wrong on our side." } }], [504, {}]])(
    "does not say nothing changed when a %s leaves the outcome unknown", async (status, body) => {
      respond(body, status);
      dialog("confirm", FLAGS, [1, 2, 3]);
      fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
      const alert = await screen.findByRole("alert");
      expect(within(alert).getByText("The change could not be confirmed")).toBeInTheDocument();
      expect(alert).not.toHaveTextContent(/not changed|Nothing was changed/);
    });

  it("shows a refusal as given and changes nothing on screen", async () => {
    respond({ error: { code: "conflict", message: "This assessment changed after you reviewed it. Nothing was changed." } }, 409);
    const onDone = dialog("confirm", FLAGS, [1, 2, 3]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm as real issues" }));
    expect(await screen.findByText("This assessment changed after you reviewed it. Nothing was changed.")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("will not act on more locations than one action takes, or on a decided report", () => {
    const { rerender } = renderWithProviders(<BulkSelectionBar slot="after" findings={3} locations={201} elsewhere={0} decided={false} onAction={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("201 locations are selected. One action takes at most 200");
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled();
    rerender(<BulkSelectionBar slot="after" findings={3} locations={3} elsewhere={0} decided onAction={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/Reopen it to change findings/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm as real issues" })).toBeDisabled();
  });

  it("selects all open findings of the check on screen only, never another check's or resolved ones", async () => {
    panel([...FLAGS, flag({ id: 4, page_anchor: "p. 50", overridden: true, evidence_excerpt: "Dismissed one" }),
      flag({ id: 5, check_kind: "internal_agreement", problem_kind: null, evidence_excerpt: "Objective and result differ", page_anchor: "p. 12" })]);
    fireEvent.click(await screen.findByRole("button", { name: /^Select all \d+ open findings? in this check/ }));
    expect(screen.getByText("3 findings selected")).toBeInTheDocument();
  });

  it("says how many select-all takes, including those not shown on screen", async () => {
    panel(Array.from({ length: 11 }, (_, i) => flag({ id: i + 1, page_anchor: `p. ${i + 1}`, evidence_excerpt: `Entry ${i + 1}` })));
    expect(await screen.findByRole("button", { name: "Select all 11 open findings in this check, including 3 not shown" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Select all 11 open findings/ }));
    expect(screen.getByText("11 findings selected")).toBeInTheDocument();
  });

  it("offers no checkbox for findings already dismissed or confirmed", async () => {
    panel([flag(), flag({ id: 2, page_anchor: "p. 46", evidence_excerpt: "Cruz, 2020", finding_confirmed: true }),
      flag({ id: 3, page_anchor: "p. 47", evidence_excerpt: "Reyes, 2019", overridden: true })]);
    fireEvent.click(await screen.findByRole("button", { name: /^All:/ }));
    expect(screen.getAllByRole("checkbox", { name: /Select finding/ })).toHaveLength(1);
  });

  it("keeps the selection when the instructor leaves and comes back, and clears it on request", async () => {
    const view = panel(FLAGS);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Select finding: .*p\. 45: Serquiña, 2025/ }));
    expect(screen.getByText("1 finding selected")).toBeInTheDocument();
    view.unmount();
    panel(FLAGS);
    expect(await screen.findByText("1 finding selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText(/finding selected/)).not.toBeInTheDocument();
    expect(document.activeElement).toBe(document.getElementById("flags-heading"));
  });
});
