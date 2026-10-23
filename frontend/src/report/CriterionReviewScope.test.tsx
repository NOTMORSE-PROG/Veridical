import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, createMemoryRouter, RouterProvider, useParams } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { RequireAuth } from "../auth/RequireAuth";
import { CriterionReviewLayout } from "./CriterionReviewScope";
import { SignalEscalatedPanel } from "./SignalReviewSections";
import { useReport } from "./useReport";
import type { ResultRowCommon } from "../api/types";

const ITEMS = [1, 2, 3].map((id) => ({
  check_result_id: id, criterion_id: id, criterion_text: `Requirement ${id}`, weight: 1,
  votes: ["Custom level", null], agreement: null, ai_majority_verdict: null,
  reason: null, review_reason: "low_confidence", evidence: [{ quote: `Excerpt ${id}`, anchor: `paragraph ${id}` }],
  levels: [{ level: 1, name: "First level", descriptor: "First descriptor", points: 1 }, { level: 2, name: "Second level", descriptor: "Second descriptor", points: 2 }],
}));
function savedRow(id: number, reason: string, level = 1): ResultRowCommon {
  return { criterion_id: id, text: `Requirement ${id}`, type: "semantic", kind: "semantic", weight: 1,
    weight_importance: "med", outcome: "passed", score: level * 50, basis: "llm", anchor: null, evidence: [], reasoning: null, reason: null,
    level: { name: level === 1 ? "First level" : "Second level", ordinal: level, points: level, max_points: 2 },
    resolution: { type: "mark_level", reason, ai_majority_verdict: null } };
}
function ReportObserver() { const { data } = useReport(Number(useParams().checkRunId)); return <p data-testid="recorded-pending">{data?.pending_review_count}</p>; }
function Panel() { return <><ReportObserver /><SignalEscalatedPanel checkRunId={Number(useParams().checkRunId)} /></>; }
function Source() { return <><ReportObserver /><h1 tabIndex={-1}>Source reading</h1><Link to="..">Return to report</Link></>; }

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  let unresolved = [...ITEMS];
  let saved: ResultRowCommon[] = [];
  let revision = 0;
  let failList = false;
  const held: Array<{ id: number; finish: (success?: boolean, response?: unknown, apply?: boolean, status?: number) => void }> = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const resolution = path.match(/\/escalated\/(\d+)\/resolve$/);
    if (resolution && init?.method === "POST") return await new Promise<Response>((resolve) => {
      const id = Number(resolution[1]);
      const body = JSON.parse(init.body as string) as { reason: string; level: number };
      const row = savedRow(id, body.reason, body.level);
      held.push({ id, finish(success = true, response?: unknown, apply = true, status = success ? 200 : 503) {
        if (success && apply) { revision += 1; unresolved = unresolved.filter((item) => item.check_result_id !== id); saved = [...saved.filter((item) => item.criterion_id !== id), row]; }
        resolve(new Response(JSON.stringify(response ?? (success
          ? { check_result_id: id, outcome: "passed", score: row.score, report: { assessment_revision: revision, decision: null, check_run_id: 5, results: saved, pending_review_count: unresolved.length } }
          : { error: { code: "internal", message: "Controlled save failure" } })), { status }));
      } });
    });
    if (path.endsWith("/escalated")) return new Response(JSON.stringify(failList ? { error: { code: "internal", message: "Controlled list failure" } } : unresolved), { status: failList ? 503 : 200 });
    if (path.endsWith("/report")) return new Response(JSON.stringify({ assessment_revision: revision, decision: null, check_run_id: Number(path.split("/")[2]), results: saved, pending_review_count: unresolved.length }));
    return new Response(JSON.stringify({ instructor: { id: 1 } }));
  });
  vi.stubGlobal("fetch", fetch);
  const router = createMemoryRouter([
    { path: "/report/:checkRunId", element: <RequireAuth><CriterionReviewLayout /></RequireAuth>, children: [
      { index: true, element: <Panel /> }, { path: "document", element: <Source /> },
    ] },
    { path: "/outside", element: <p>Outside review</p> },
    { path: "/signin", element: <p>Sign in</p> },
    { path: "/shared/test", element: <p>Shared read only</p> },
  ], { initialEntries: ["/report/5"] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { client, router, fetch, held, supersede() { revision += 1; }, failList() { failList = true; }, restoreList() { failList = false; } };
}

async function card(id: number) { return (await screen.findByRole("heading", { name: `Requirement ${id}` })).closest("article")!; }
async function draft(id: number, level: string, reason: string) {
  const root = within(await card(id));
  fireEvent.click(root.getByRole("button", { name: level }));
  const text = root.getByRole("textbox");
  fireEvent.change(text, { target: { value: reason } });
  return text;
}
async function sourceFrom(id: number) {
  const link = within(await card(id)).getByRole("link", { name: /Open in manuscript/ });
  link.focus(); fireEvent.click(link);
  await screen.findByRole("heading", { name: "Source reading" });
}
async function confirm(id: number) {
  const button = within(await card(id)).getByRole("button", { name: "Confirm resolution" });
  button.focus(); fireEvent.click(button);
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("report-family private review ownership", () => {
  it.each([
    { check_result_id: 1, outcome: "passed", report: { check_run_id: 5 } },
    { check_result_id: 88, outcome: "passed", report: { check_run_id: 5, results: [] } },
    { check_result_id: 1, outcome: "passed", report: { check_run_id: 77, results: [] } },
    ...["escalated", "api_down", "quota_exhausted", "unverifiable"].map((outcome) => ({ check_result_id: 1, outcome, report: { check_run_id: 5, results: [] } })),
  ])("retains a draft for an unconfirmed or mismatched success record $check_result_id", async (response) => {
    const { held, client } = setup(); await draft(1, "First level", "Exact reason until an authoritative result"); await confirm(1);
    await act(async () => { held[0].finish(true, response, false); });
    expect(await screen.findByText("Saving could not be confirmed")).toBeVisible();
    // The control's server list also reports it unresolved; no success inferred.
    expect(within(await card(1)).getByRole("textbox")).toHaveValue("Exact reason until an authoritative result");
    expect(client.getQueryData(["report", 5])).not.toEqual(response.report);
    expect(screen.queryByText(/Criterion resolved as/)).not.toBeInTheDocument();
  });

  it("retains the losing sibling draft until current review binds it to the winner's newer revision", async () => {
    const { held, client, fetch } = setup();
    await draft(1, "First level", "First independent confirmation"); await confirm(1);
    await draft(2, "Second level", "Second independent confirmation"); await confirm(2);
    await act(async () => { held[1].finish(); });
    await act(async () => { held[0].finish(false, { error: { code: "conflict", message: "Assessment changed" } }, false, 409); });
    await waitFor(() => expect(client.getQueryData(["report", 5])).toMatchObject({ assessment_revision: 1, pending_review_count: 2 }));
    const losing = within(await card(1));
    expect(losing.getByRole("textbox")).toHaveValue("First independent confirmation");
    expect(losing.getByRole("button", { name: "Confirm resolution" })).toBeDisabled();
    const posts = () => fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts().map(([, init]) => JSON.parse(String(init?.body)).expected_revision)).toEqual([0, 0]);
    fireEvent.click(losing.getByRole("button", { name: "Review current criterion" }));
    fireEvent.click(await losing.findByRole("button", { name: "Use this draft for the current criterion" }));
    expect(posts()).toHaveLength(2);
    await confirm(1);
    await waitFor(() => expect(held).toHaveLength(3));
    expect(JSON.parse(String(posts()[2][1]?.body)).expected_revision).toBe(1);
    await act(async () => { held[2].finish(); });
    await waitFor(() => expect(client.getQueryData(["report", 5])).toMatchObject({ assessment_revision: 2, pending_review_count: 1 }));
  });

  it.each([1, 3])("retains two exact sibling choices through source from item %s and browser Back", async (sourceId) => {
    const { router, fetch } = setup();
    await draft(1, "First level", "  first reason\nwith whitespace  ");
    await draft(2, "Second level", "Second reason stays separate");
    await sourceFrom(sourceId);
    await act(async () => { await router.navigate(-1); });
    expect(within(await card(1)).getByRole("textbox")).toHaveValue("  first reason\nwith whitespace  ");
    expect(within(await card(2)).getByRole("textbox")).toHaveValue("Second reason stays separate");
    expect(within(await card(1)).getByText("Selected level: First level")).toBeVisible();
    expect(within(await card(2)).getByText("Selected level: Second level")).toBeVisible();
    expect(document.activeElement?.tagName).not.toBe("TEXTAREA");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(JSON.stringify(router.state.location)).not.toContain("reason");
  });

  it("retains attempted validation through source and the in-app return", async () => {
    setup(); await draft(1, "First level", ""); await confirm(1);
    expect(screen.getByText("Enter a reason before confirming.")).toBeVisible();
    await sourceFrom(1); fireEvent.click(screen.getByRole("link", { name: "Return to report" }));
    expect(await screen.findByText("Enter a reason before confirming.")).toBeVisible();
  });

  it.each([true, false])("handles save completion while the card is absent (success=%s)", async (success) => {
    const { held } = setup();
    await draft(1, "First level", "First exact reason"); await draft(2, "Second level", "Second exact reason");
    await confirm(1); await waitFor(() => expect(held).toHaveLength(1));
    await sourceFrom(3);
    const source = screen.getByRole("heading", { name: "Source reading" }); source.focus();
    await act(async () => { held[0].finish(success); });
    expect(source).toHaveFocus();
    fireEvent.click(screen.getByRole("link", { name: "Return to report" }));
    expect(within(await card(2)).getByRole("textbox")).toHaveValue("Second exact reason");
    if (success) {
      await waitFor(() => expect(screen.queryByRole("heading", { name: "Requirement 1" })).not.toBeInTheDocument());
      expect(await screen.findByText(/Criterion resolved as First level/)).toBeInTheDocument();
    } else {
      expect(within(await card(1)).getByRole("textbox")).toHaveValue("First exact reason");
      expect(await screen.findByText("Saving could not be confirmed")).toBeVisible();
      const currentCard = within(await card(1));
      fireEvent.click(currentCard.getByRole("button", { name: "Review current criterion" }));
      fireEvent.click(await currentCard.findByRole("button", { name: "Use this draft for the current criterion" }));
      await confirm(1); await waitFor(() => expect(held).toHaveLength(2));
      expect(held.map((entry) => entry.id)).toEqual([1, 1]);
    }
  });

  it("restores pending state, prevents duplicates, and lets a sibling keep focus on completion", async () => {
    const { held, router } = setup();
    await draft(1, "First level", "First reason for save"); await confirm(1);
    await sourceFrom(3); await act(async () => { await router.navigate(-1); });
    const pending = within(await card(1));
    expect(pending.getByRole("textbox")).toBeDisabled();
    expect(pending.getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.click(pending.getByRole("button", { name: "Confirm resolution" }));
    expect(held).toHaveLength(1);
    const sibling = await draft(2, "Second level", "Typing the independent second reason"); sibling.focus();
    await act(async () => { held[0].finish(); });
    expect(sibling).toHaveFocus(); expect(sibling).toHaveValue("Typing the independent second reason");
  });

  it("does not prune drafts when the unresolved list fails", async () => {
    const state = setup(); await draft(1, "First level", "Retain through failed list request");
    state.failList();
    await act(async () => { await state.client.refetchQueries({ queryKey: ["report", 5, "escalated"] }); });
    expect(await screen.findByText("Could not update criteria needing review")).toBeVisible();
    state.restoreList(); fireEvent.click(screen.getByRole("button", { name: "Retry review items" }));
    expect(within(await card(1)).getByRole("textbox")).toHaveValue("Retain through failed list request");
  });

  it.each(["run", "outside", "public"])("retires drafts only after explicit discard on %s scope exit", async (exit) => {
    const { router } = setup(); await draft(1, "First level", "Private reason must retire");
    await act(async () => { await router.navigate(exit === "run" ? "/report/6" : exit === "public" ? "/shared/test" : "/outside"); });
    expect(await screen.findByRole("dialog", { name: "Leave this review with unsaved changes?" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/report/5");
    fireEvent.click(screen.getByRole("button", { name: "Leave without saving" }));
    expect(screen.queryByDisplayValue("Private reason must retire")).not.toBeInTheDocument();
    await act(async () => { await router.navigate("/report/5"); });
    await card(1); expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps both exact drafts and the triggering action after cancelling an exit", async () => {
    const { router, fetch } = setup();
    await draft(1, "First level", "  Exact first reason\nkept  ");
    await draft(2, "Second level", "Second separate reason");
    const trigger = within(await card(2)).getByRole("button", { name: "Confirm resolution" }); trigger.focus();
    await act(async () => { await router.navigate("/outside"); });
    fireEvent.click(await screen.findByRole("button", { name: "Keep reviewing" }));
    expect(router.state.location.pathname).toBe("/report/5");
    expect(within(await card(1)).getByRole("textbox")).toHaveValue("  Exact first reason\nkept  ");
    expect(within(await card(2)).getByRole("textbox")).toHaveValue("Second separate reason");
    expect(trigger).toHaveFocus();
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("retains an active save owner and requires a fresh exit intent after success", async () => {
    const { router, held } = setup(); await draft(1, "First level", "Saving is not cancellation"); await confirm(1);
    await act(async () => { await router.navigate("/outside"); });
    const dialog = await screen.findByRole("dialog", { name: "Stay in this review" });
    expect(within(dialog).queryByRole("button", { name: "Leave without saving" })).not.toBeInTheDocument();
    await act(async () => { held[0].finish(); });
    expect(router.state.location.pathname).toBe("/report/5");
    expect(await screen.findByText("The review status has changed. Select Keep reviewing, then choose your destination again.")).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep reviewing" }));
    await act(async () => { await router.navigate("/outside"); });
    expect(await screen.findByText("Outside review")).toBeVisible();
  });

  it("keeps an ambiguous submission protected after a negative read at the same revision", async () => {
    const { router, held, supersede } = setup(); await draft(1, "First level", "Ambiguous original submission"); await confirm(1);
    await act(async () => { held[0].finish(false); });
    const root = within(await card(1));
    fireEvent.click(root.getByRole("button", { name: "Review current criterion" }));
    await root.findByRole("button", { name: "Use this draft for the current criterion" });
    expect(root.getByRole("button", { name: "Discard this draft" })).toBeDisabled();
    await act(async () => { await router.navigate("/outside"); });
    const dialog = await screen.findByRole("dialog", { name: "Stay in this review" });
    expect(within(dialog).getByText(/Select Keep reviewing, then review the current criterion/)).toBeVisible();
    expect(within(dialog).queryByRole("button", { name: "Leave without saving" })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep reviewing" }));
    // A newer assessment fences the old expected revision; the draft still needs an explicit choice.
    supersede();
    fireEvent.click(root.getByRole("button", { name: "Review current criterion" }));
    await waitFor(() => expect(root.getByRole("button", { name: "Discard this draft" })).toBeEnabled());
    await act(async () => { await router.navigate("/outside"); });
    fireEvent.click(await screen.findByRole("button", { name: "Leave without saving" }));
    expect(await screen.findByText("Outside review")).toBeVisible();
  });

  it("allows explicit discard after a definitive stale refusal", async () => {
    const { router, held } = setup(); await draft(1, "First level", "Definitive rollback refusal"); await confirm(1);
    await act(async () => { held[0].finish(false, undefined, false, 409); });
    await act(async () => { await router.navigate("/outside"); });
    fireEvent.click(await screen.findByRole("button", { name: "Leave without saving" }));
    expect(await screen.findByText("Outside review")).toBeVisible();
  });

  it.each([1, 2])("fences a late response after auth cache replacement for instructor %s", async (nextId) => {
    const { client, held, fetch } = setup(); await draft(1, "First level", "Old lifetime private reason"); await confirm(1);
    await waitFor(() => expect(held).toHaveLength(1));
    await act(async () => { client.clear(); client.setQueryData(ME_QUERY_KEY, { id: nextId }); });
    await card(1);
    await waitFor(() => expect(client.isFetching()).toBe(0));
    const currentRecord = client.getQueryData(["report", 5]);
    const requestCount = fetch.mock.calls.length;
    await act(async () => { held[0].finish(); });
    expect(client.getQueryData(["report", 5])).toBe(currentRecord);
    expect(fetch.mock.calls).toHaveLength(requestCount);
    expect(screen.queryByDisplayValue("Old lifetime private reason")).not.toBeInTheDocument();
    expect(screen.queryByText(/Criterion resolved as/)).not.toBeInTheDocument();
  });

  it("fences a late response after confirmed auth loss", async () => {
    const { client, held } = setup(); await draft(1, "First level", "Retire on confirmed auth loss"); await confirm(1);
    await act(async () => { client.setQueryData(ME_QUERY_KEY, null); });
    expect(await screen.findByText("Sign in")).toBeVisible();
    const currentRecord = client.getQueryData(["report", 5]);
    await act(async () => { held[0].finish(); });
    expect(client.getQueryData(["report", 5])).toBe(currentRecord);
    expect(screen.queryByText(/Criterion resolved as/)).not.toBeInTheDocument();
  });
});

it.each(["unconfirmed response", "service failure"])("requires current review and explicit rebind after %s", async failure => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const control = setup(); const reason = "  Exact reason preserved through uncertain save\nwith whitespace  ";
  await draft(1, "First level", reason); await confirm(1);
  await waitFor(() => expect(control.held).toHaveLength(1));
  await act(async () => {
    if (failure === "service failure") control.held[0].finish(false);
    else control.held[0].finish(true, { check_result_id: 1, outcome: "passed", report: { check_run_id: 5, results: [], assessment_revision: 1 } }, false);
  });
  const root = within(await card(1));
  await waitFor(() => expect(root.getByRole("button", { name: "Confirm resolution" })).toBeDisabled());
  expect(root.getByRole("textbox")).toHaveValue(reason);
  fireEvent.click(root.getByRole("button", { name: "Review current criterion" }));
  const rebind = await root.findByRole("button", { name: "Use this draft for the current criterion" });
  rebind.focus(); fireEvent.click(rebind);
  expect(root.getByRole("textbox")).toHaveFocus();
  await waitFor(() => expect(root.getByRole("button", { name: "Confirm resolution" })).toBeEnabled());
  expect(root.getByRole("textbox")).toHaveValue(reason);
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

it("shows a decided-state draft route and requires explicit current review after reopening", async () => {
  const control = setup(); await draft(1, "First level", "Exact retained draft reason");
  await act(async () => { control.client.setQueryData(["report", 5], { check_run_id: 5, assessment_revision: 1, decision: "returned", results: [], pending_review_count: 3 }); });
  expect(await screen.findByRole("heading", { name: "Unresolved at time of return" })).toBeVisible();
  const root = within(await card(1));
  expect(root.queryByRole("button", { name: "Review current criterion" })).not.toBeInTheDocument();
  expect(root.getByRole("link", { name: "Go to the recorded decision" })).toHaveAttribute("href", "/report/5#final-decision");
  expect(root.getByRole("textbox")).toHaveAttribute("readonly");
  expect(screen.queryByText(/Reopening did not save/)).not.toBeInTheDocument();
  await act(async () => { control.client.setQueryData(["report", 5], { check_run_id: 5, assessment_revision: 2, decision: null, results: [], pending_review_count: 3 }); });
  expect(root.getByRole("button", { name: "Confirm resolution" })).toBeDisabled();
  expect(await root.findByRole("button", { name: "Review current criterion" })).toBeEnabled();
  expect(root.getByRole("textbox")).toHaveValue("Exact retained draft reason");
});
