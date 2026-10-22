import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import type { EscalatedItemOut, ResultRowCommon } from "../api/types";
import { renderWithProviders, renderWithReviewProviders } from "../test/renderWithProviders";
import { SignalCriteriaResults, SignalEscalatedPanel } from "./SignalReviewSections";
import { useReport } from "./useReport";

const items: EscalatedItemOut[] = [1, 2].map((id) => ({ check_result_id: id, criterion_id: id + 100,
  criterion_text: `Custom requirement ${id}`, weight: 1, agreement: null, votes: [], ai_majority_verdict: null,
  reason: null, review_reason: "low_confidence", unverified_evidence: null,
  evidence: [{ quote: `Recorded source ${id}`, anchor: `paragraph ${id}` }] }));
const result = (id: number, extra: Partial<ResultRowCommon> = {}): ResultRowCommon => ({ criterion_id: id + 100,
  text: `Custom requirement ${id}`, type: "semantic", kind: "semantic", weight: 1, weight_importance: "med",
  outcome: "escalated", score: null, basis: "llm", anchor: null, evidence: [], reasoning: null, reason: null, resolution: null, ...extra });

function Review() {
  const query = useReport(5);
  const location = useLocation();
  return <><SignalEscalatedPanel checkRunId={5} /><details><summary>Recorded criterion results</summary>
    <SignalCriteriaResults checkRunId={5} results={query.data?.results.filter((row) => row.outcome !== "escalated") ?? []} />
  </details><button>Another task</button><p data-testid="current-route">{location.search}</p></>;
}

function setup() {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  let rows = [result(1), result(2), result(3, { outcome: "passed", score: 100, basis: "rule" })];
  let holdReport = false;
  let failReport = false;
  let staleReport = false;
  let staleList = false;
  let failList = 0;
  const reports: Array<(response: Response) => void> = [];
  const posts: Array<{ id: number; finish: (success?: boolean) => void }> = [];
  const report = () => ({ check_run_id: 5, results: rows, pending_review_count: rows.filter((row) => row.outcome === "escalated").length });
  const reportResponse = () => new Response(JSON.stringify(failReport ? { error: { code: "unavailable", message: "Controlled report read failure" } }
    : staleReport ? { ...report(), results: [result(3, { outcome: "passed", score: 100, basis: "rule" })] } : report()), { status: failReport ? 503 : 200 });
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const resolve = path.match(/\/escalated\/(\d+)\/resolve$/);
    if (resolve && init?.method === "POST") return await new Promise<Response>((finish) => {
      const id = Number(resolve[1]);
      const body = JSON.parse(init.body as string) as { resolution: "mark_pass" | "needs_document"; reason: string };
      posts.push({ id, finish(success = true) {
        if (!success) { finish(new Response(JSON.stringify({ error: { code: "unavailable", message: "Controlled save failure" } }), { status: 503 })); return; }
        const row = result(id, { outcome: body.resolution === "needs_document" ? "not_applicable" : "passed",
          score: body.resolution === "needs_document" ? null : 100,
          resolution: { type: body.resolution, reason: body.reason, ai_majority_verdict: null } });
        rows = rows.map((old) => old.criterion_id === row.criterion_id ? row : old);
        finish(new Response(JSON.stringify({ check_result_id: id, outcome: row.outcome, score: row.score, report: report() })));
      } });
    });
    if (path.endsWith("/report")) return holdReport ? await new Promise<Response>((finish) => reports.push(finish)) : reportResponse();
    if (path.endsWith("/escalated") && failList) return new Response(JSON.stringify({ error: { code: "unavailable", message: "Controlled review-list failure" } }), { status: failList });
    if (path.endsWith("/escalated")) return new Response(JSON.stringify(staleList ? items : items.filter((item) => rows.find((row) => row.criterion_id === item.criterion_id)?.outcome === "escalated")));
    return new Response(JSON.stringify({ instructor: { id: 1 } }));
  });
  vi.stubGlobal("fetch", fetch);
  const rendered = renderWithReviewProviders(<Review />, { route: "/report/5" });
  return { ...rendered, fetch, posts, reports,
    hold() { holdReport = true; },
    release(success = true, stale = false) { holdReport = false; failReport = !success; staleReport = stale; reports.splice(0).forEach((finish) => finish(reportResponse())); },
    restoreReads() { failReport = false; staleReport = false; },
    keepOldReviewList() { staleList = true; },
    failReviewList(status = 503) { failList = status; },
    recoverReviewList() { failList = 0; },
    restoreReviewList() { staleList = false; },
  };
}

async function confirm(id: number, exclusion = false) {
  const card = (await screen.findByRole("heading", { name: `Custom requirement ${id}` })).closest("article")!;
  fireEvent.click(within(card).getByRole("button", { name: exclusion ? "Exclude: needs another document" : "Meets criterion" }));
  fireEvent.change(within(card).getByRole("textbox"), { target: { value: `Private reason for requirement ${id}.` } });
  const button = within(card).getByRole("button", { name: exclusion ? "Confirm exclusion" : "Confirm resolution" });
  button.focus(); fireEvent.click(button);
  return button;
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("saved judgment retrieval", () => {
  it("acknowledges a save before a held report, qualifies the count and reveals only the refreshed exact row", async () => {
    const control = setup();
    await screen.findByText("No saved instructor judgments yet.");
    control.hold();
    const button = await confirm(1);
    expect(button).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Criteria needing your judgment" })).toHaveFocus();
    await act(async () => { control.posts[0].finish(); });
    expect(await screen.findByRole("heading", { name: "Judgment saved" })).toHaveFocus();
    expect(screen.getByText("Last loaded: 0 saved instructor judgments.")).toBeVisible();
    expect(screen.queryByText("No saved instructor judgments yet.")).not.toBeInTheDocument();
    const view = screen.getByRole("button", { name: "View saved judgment: Custom requirement 1" });
    view.focus(); fireEvent.click(view);
    expect(await screen.findByText("Loading the recorded judgment. Your saved acknowledgment remains above.")).toBeVisible();
    expect(document.getElementById("criterion-record-5-101")).toBeNull();
    expect(screen.queryByText("That recorded criterion is not available in this report.")).not.toBeInTheDocument();
    await act(async () => { control.release(); });
    await waitFor(() => expect(document.getElementById("criterion-record-5-101")).toHaveFocus());
    expect(screen.getByText("Private reason for requirement 1.")).toBeVisible();
    expect(screen.getByRole("button", { name: "View saved judgments (1)" })).toBeVisible();
  });

  it("retires delayed reveal and focus after another task while its report is held", async () => {
    const control = setup(); await screen.findByText("No saved instructor judgments yet."); control.hold();
    await confirm(1); await act(async () => { control.posts[0].finish(); });
    const view = await screen.findByRole("button", { name: "View saved judgment: Custom requirement 1" });
    view.focus(); fireEvent.click(view);
    await screen.findByText("Loading the recorded judgment. Your saved acknowledgment remains above.");
    const other = screen.getByRole("button", { name: "Another task" }); other.focus();
    const disclosure = screen.getByText("Recorded criterion results").closest("details")!; disclosure.open = false;
    await act(async () => { control.release(); });
    await waitFor(() => expect(document.getElementById("criterion-record-5-101")).not.toBeNull());
    expect(other).toHaveFocus(); expect(disclosure.open).toBe(false);
  });

  it("keeps focused receipt A bound to A when sibling B completes", async () => {
    const control = setup(); await confirm(1); await act(async () => { control.posts[0].finish(); });
    await screen.findByRole("button", { name: "View saved judgments (1)" });
    await confirm(2);
    const viewA = screen.getByRole("button", { name: "View saved judgment: Custom requirement 1" }); viewA.focus();
    await act(async () => { control.posts[1].finish(); });
    await screen.findByRole("button", { name: "View saved judgments (2)" });
    expect(viewA).toHaveFocus(); expect(viewA).toHaveAccessibleName("View saved judgment: Custom requirement 1");
    fireEvent.click(viewA);
    await waitFor(() => expect(document.getElementById("criterion-record-5-101")).toHaveFocus());
    expect(screen.getByTestId("current-route")).toHaveTextContent("criterion_record=101");
  });

  it("preserves focused receipt A when B completes and the background review-list read fails", async () => {
    const control = setup(); await confirm(1); await act(async () => { control.posts[0].finish(); });
    await screen.findByRole("button", { name: "View saved judgments (1)" });
    await confirm(2);
    const viewA = screen.getByRole("button", { name: "View saved judgment: Custom requirement 1" }); viewA.focus();
    control.failReviewList(); await act(async () => { control.posts[1].finish(); });
    await screen.findByText("Could not update criteria needing review");
    expect(viewA.isConnected).toBe(true); expect(viewA).toHaveFocus();
    expect(viewA).toHaveAccessibleName("View saved judgment: Custom requirement 1");
    expect(screen.getByText("Review list needs refreshing")).toBeVisible();
    expect(screen.queryByText("No unresolved criteria")).not.toBeInTheDocument();
    control.recoverReviewList(); fireEvent.click(screen.getByRole("button", { name: "Retry review items" }));
    await screen.findByText("No unresolved criteria");
    expect(viewA.isConnected).toBe(true); expect(viewA).toHaveFocus();
  });

  it.each([401, 403, 404])("hides cached review cards and receipt when the list read denies access with %s", async (status) => {
    const control = setup(); await confirm(1); await act(async () => { control.posts[0].finish(); });
    await screen.findByRole("heading", { name: "Judgment saved" });
    await confirm(2); control.failReviewList(status);
    await act(async () => { control.posts[1].finish(); });
    await screen.findByText("Could not load criteria needing review");
    expect(screen.queryByRole("heading", { name: "Judgment saved" })).not.toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /View saved judgment/ })).not.toBeInTheDocument();
    expect(screen.getByText("Review list unavailable")).toBeVisible();
  });

  it("distinguishes saved judgment from a failed read and retries only the GET", async () => {
    const control = setup(); await screen.findByText("No saved instructor judgments yet."); control.hold();
    await confirm(1); await act(async () => { control.posts[0].finish(); });
    const view = await screen.findByRole("button", { name: "View saved judgment: Custom requirement 1" }); view.focus(); fireEvent.click(view);
    await act(async () => { control.release(false); });
    expect(await screen.findByText("Your judgment was saved, but the recorded result could not be loaded.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Judgment saved" })).toBeVisible();
    control.restoreReads(); fireEvent.click(screen.getByRole("button", { name: "Retry recorded result" }));
    await screen.findByRole("button", { name: "View saved judgments (1)" });
    expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("has no receipt or saved-count increment when the save fails", async () => {
    const control = setup(); const button = await confirm(1);
    await act(async () => { control.posts[0].finish(false); });
    await screen.findByText("Could not save this resolution");
    expect(button).toBeEnabled();
    expect(screen.getByRole("textbox")).toHaveValue("Private reason for requirement 1.");
    expect(screen.queryByRole("heading", { name: "Judgment saved" })).not.toBeInTheDocument();
    expect(screen.getByText("No saved instructor judgments yet.")).toBeVisible();
  });

  it("does not call a successfully loaded older snapshot zero saved judgments after a confirmed save", async () => {
    const control = setup(); await screen.findByText("No saved instructor judgments yet."); control.hold();
    await confirm(1); await act(async () => { control.posts[0].finish(); });
    await act(async () => { control.release(true, true); });
    expect(await screen.findByText("Your judgment was saved, but the loaded report does not confirm every saved result.")).toBeVisible();
    expect(screen.getByText("Last loaded: 0 saved instructor judgments.")).toBeVisible();
    expect(screen.queryByText("No saved instructor judgments yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("No criterion records in this section")).not.toBeInTheDocument();
    control.restoreReads(); fireEvent.click(screen.getByRole("button", { name: "Retry saved judgments" }));
    await screen.findByRole("button", { name: "View saved judgments (1)" });
    expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("does not offer an already-confirmed criterion again when the unresolved-list read is older", async () => {
    const control = setup(); control.keepOldReviewList(); await confirm(1);
    await act(async () => { control.posts[0].finish(); });
    await screen.findByRole("heading", { name: "Judgment saved" });
    expect(document.getElementById("signal-resolution-heading-1")).toBeNull();
    expect(document.getElementById("signal-resolution-heading-2")).not.toBeNull();
    expect(screen.getByText("Review list needs refreshing")).toBeVisible();
    control.restoreReviewList(); fireEvent.click(screen.getByRole("button", { name: "Refresh review items" }));
    await screen.findByText("1 unresolved");
    expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("keeps an exclusion ungraded in its receipt and canonical record", async () => {
    const control = setup(); await confirm(1, true); await act(async () => { control.posts[0].finish(); });
    const view = await screen.findByRole("button", { name: "View saved judgment: Custom requirement 1" }); view.focus(); fireEvent.click(view);
    await waitFor(() => expect(document.getElementById("criterion-record-5-101")).toHaveFocus());
    expect(screen.getAllByText("Excluded from readiness calculation: needs another document. No grade recorded.")).toHaveLength(2);
    expect(screen.queryByText("Recorded grade: Meets criterion")).not.toBeInTheDocument();
  });

  it("keeps private reasons, cause and saved navigation out of public rendering even with an injected private row", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={5} publicView results={[result(1, { outcome: "not_applicable", resolution: {
      type: "needs_document", reason: "Private hidden reason", ai_majority_verdict: null,
    } })]} />);
    expect(screen.queryByText("Private hidden reason")).not.toBeInTheDocument();
    expect(screen.queryByText("Instructor judgment")).not.toBeInTheDocument();
    expect(screen.queryByText(/Excluded from readiness calculation/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /saved judgment/ })).not.toBeInTheDocument();
  });
});
