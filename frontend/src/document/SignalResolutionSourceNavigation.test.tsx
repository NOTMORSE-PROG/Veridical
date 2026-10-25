import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SignalReportPage } from "../report/SignalReport";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";
import { stubFetchByPath } from "../test/renderWithProviders";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { clearRouteReturnFocus } from "../routing/useRouteFocus";
import { CriterionReviewLayout } from "../report/CriterionReviewScope";
import type { CriterionSourceCoverage, ResultRowCommon } from "../api/types";

vi.mock("./PdfPane", () => ({ PdfPane: ({ onReadText }: { onReadText?: () => void }) => <div>PDF source<button onClick={onReadText}>Read text</button></div> }));
vi.mock("./ReuseExplorePanel", () => ({ ReuseExplorePanel: () => null }));

const ORIGIN = "/report/5/document?view=decision&pane=review&filter=all&sentinel=kept";
const ITEMS = [1, 2].map((id) => ({
  check_result_id: id, criterion_id: id, criterion_text: `Criterion ${id}`, weight: 1,
  votes: ["pass", "fail", "pass"], agreement: 0.667, ai_majority_verdict: "pass",
  reasoning: `Saved explanation ${id}`, reason: null, review_reason: "low_confidence",
  evidence: [{ quote: `Recorded quote ${id}`, anchor: `paragraph ${id + 1}` }],
}));
const REPORT = {
  assessment_revision: 0,
  check_run_id: 5, manuscript_id: 10, manuscript_group_label: "Local navigation control",
  manuscript_original_filename: "source.docx", rubric_title: "Custom format", status: "needs_review",
  composite_score: null, thresholds: {}, reason: null, flag_deduction: 0,
  unresolved_high_flag_count: 0, decision: null, decided_at: null, decision_note: null,
  pending_review_count: 2, rubric_is_current: true, llm_mode: "real", rubric_needs_review: false,
  rubric_parse_issues: null, previous_status: null, previous_composite_score: null,
  integrity_check_status: [], results: [],
};
const PARAGRAPHS = { paragraphs: [{ paragraph: 2, text: "The requested source paragraph", heading_level: null }, { paragraph: 3, text: "A second source paragraph", heading_level: null }], tables: [], images: [] };
const SAVED: ResultRowCommon = { criterion_id: 77, text: "Previously saved custom criterion", type: "semantic", kind: "semantic",
  weight: 1, weight_importance: "med", outcome: "passed", score: 100, basis: "llm", anchor: null, reasoning: null, reason: null,
  evidence: [{ quote: "Recorded source for saved judgment", anchor: "paragraph 2" }],
  resolution: { type: "mark_pass", reason: "A private saved instructor reason.", ai_majority_verdict: "fail" } };
const COVERAGE: CriterionSourceCoverage = { native_math_coverage_version: "v1", native_math_inventory_state: "complete",
  native_math_inventory_scope: "main_document_body", n_native_equations_unassessed: 2, n_native_equations_unlocated: 0,
  native_math_excluded_paragraphs: [2, 3], native_math_coverage_items: [
    { paragraph: 2, kind: "paragraph", n_occurrences: 1 }, { paragraph: 3, kind: "paragraph", n_occurrences: 1 }] };

it("retires a saved destination when explicitly returning to unresolved criteria, keeping both drafts", async () => {
  const control = await setup({ open: false, saved: true });
  const view = await screen.findByRole("button", { name: "View saved judgments (1)" }); view.focus(); fireEvent.click(view);
  await waitFor(() => expect(control.router.state.location.search).toContain("criterion_record=all"));
  const queue = screen.getByRole("button", { name: "Back to review queue" }); queue.focus(); fireEvent.click(queue);
  await screen.findByRole("button", { name: "Review criteria" });
  expect(control.router.state.location.search).not.toContain("criterion_record");
  const criteria = screen.getByRole("button", { name: "Review criteria" }); criteria.focus(); fireEvent.click(criteria);
  await waitFor(() => expect(document.getElementById("escalated-heading")).toHaveFocus());
  expect(control.router.state.location.search).not.toContain("criterion_record");
  expect(screen.getByDisplayValue("First private draft")).toBeVisible();
  expect(screen.getByDisplayValue("Second private draft")).toBeVisible();
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});

async function setup({ origin = ORIGIN, open = true, drafts: makeDrafts = true, saved = false, instructor = true, full = false, pdf = false, coverage = undefined as CriterionSourceCoverage | undefined } = {}) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const scroll = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  let release!: (body: typeof PARAGRAPHS) => void;
  const held = new Promise<typeof PARAGRAPHS>((resolve) => { release = resolve; });
  const pendingItems = ITEMS.map(item => ({ ...item, assessment_revision: 0, evidence: pdf ? [{ quote: item.evidence[0].quote, anchor: "page 3" }] : item.evidence }));
  const pendingRows = pendingItems.map(item => ({ ...SAVED, criterion_id: item.criterion_id, check_result_id: item.check_result_id,
    text: item.criterion_text, outcome: "escalated", resolution: null, source_coverage: coverage }));
  let pendingReply: (() => Promise<Response>) | undefined;
  const normal = stubFetchByPath({
    "/check-runs/5/document": { manuscript_id: 10, source_format: pdf ? "pdf" : "docx", available: true, original_filename: pdf ? "source.pdf" : "source.docx", page_count: pdf ? 6 : null, regions: [] },
    "/check-runs/5/share": null,
    "/check-runs/5/report": { ...REPORT, results: saved ? [{ ...SAVED, source_coverage: coverage, resolution: instructor ? SAVED.resolution : null }] : coverage ? pendingRows : [] }, "/check-runs/5/flags": [], "/check-runs/5/escalated": pendingItems,
    "/check-runs/5/document/reuse-matches": { matches: [], passage_archive_size_n: 0 },
  });
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/document/page-text")) {
      const page = Number(new URL(String(input), "http://localhost").searchParams.get("page"));
      return new Response(JSON.stringify({ check_run_id: 5, manuscript_id: 10, page, page_count: 6,
        state: "available", reason: null, blocks: [{ text: `Saved source text page ${page}`, is_furniture: false }], saved_block_count: 1, omitted_block_count: 0 }));
    }
    if (String(input).endsWith("/escalated") && pendingReply) return pendingReply();
    if (String(input).includes("/document/paragraphs")) return new Response(JSON.stringify(await held), { status: 200 });
    return normal(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  const router = createMemoryRouter([{ path: "/report/:checkRunId", element: <CriterionReviewLayout />, children: [{ index: true, element: <SignalReportPage /> }, { path: "document", element: <SignalDocumentViewerPage /> }] }], { initialEntries: [{ pathname: full ? "/report/5" : "/report/5/document", search: origin.slice(origin.indexOf("?")), state: { notice: "retained" } }] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  const choices = await screen.findAllByRole("button", { name: "Meets criterion" });
  if (makeDrafts) {
    fireEvent.click(choices[0]);
    fireEvent.click(choices[1]);
  }
  const drafts = screen.queryAllByRole("textbox");
  if (makeDrafts) {
    fireEvent.change(drafts[0], { target: { value: "First private draft" } });
    fireEvent.change(drafts[1], { target: { value: "Second private draft" } });
  }
  const action = document.getElementById("signal-resolution-source-5-1-0")!;
  if (open) {
    action.focus();
    fireEvent.click(action);
    await waitFor(() => expect(router.state.location.search).toContain(pdf ? "anchor=page+3" : "anchor=paragraph+2"));
  }
  return { client, router, scroll, release, drafts, action, fetch, setPendingReply: (reply: () => Promise<Response>) => { pendingReply = reply; } };
}

afterEach(() => { clearRouteReturnFocus("signal-saved-source-"); clearRouteReturnFocus("signal-resolution-source-"); clearRouteReturnFocus("signal-resolution-coverage-"); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("retires a source return on an explicit queue task before delayed source data and Back", async () => {
  const control = await setup();
  const queue = screen.getByRole("button", { name: "Back to review queue" }); queue.focus(); fireEvent.click(queue);
  const criteria = await screen.findByRole("button", { name: "Review criteria" }); criteria.focus();
  await act(async () => control.release(PARAGRAPHS));
  expect(criteria).toHaveFocus();
  await act(async () => { await control.router.navigate(-1); await control.router.navigate(-1); });
  expect(document.getElementById("signal-resolution-source-5-1-0")).not.toHaveFocus();
  expect(screen.getByDisplayValue("First private draft")).toHaveValue("First private draft");
});

it("does not register a Back focus claim for an unowned source activation", async () => {
  const control = await setup({ open: false });
  expect(control.action).not.toHaveFocus(); fireEvent.click(control.action);
  await act(async () => control.release(PARAGRAPHS));
  await screen.findByText("The requested source paragraph");
  await act(async () => { await control.router.navigate(-1); });
  expect(document.getElementById("signal-resolution-source-5-1-0")).not.toHaveFocus();
  expect(screen.getByDisplayValue("Second private draft")).toHaveValue("Second private draft");
});

it.each(["explicit return", "browser Back"])("restores the exact nested coverage action with both drafts through %s", async mode => {
  const control = await setup({ open: false, saved: true, coverage: COVERAGE });
  const view = await screen.findByRole("button", { name: "View saved judgments (1)" }); view.focus(); fireEvent.click(view);
  const outer = screen.getByLabelText("Earlier AI assessment: source content omitted: Previously saved custom criterion").closest("details")!;
  outer.open = true;
  const nested = screen.getByLabelText("Source scope and locations: Previously saved custom criterion").closest("details")!;
  nested.open = true;
  const action = document.getElementById("signal-saved-source-5-77-2")!;
  expect(document.querySelectorAll("#signal-saved-source-5-77-2")).toHaveLength(1);
  expect(document.getElementById("signal-saved-source-5-77-3")).not.toBeNull();
  action.focus(); fireEvent.click(action);
  await act(async () => control.release(PARAGRAPHS));
  await screen.findByText("The requested source paragraph");
  outer.open = false; nested.open = false;
  if (mode === "browser Back") await act(async () => { await control.router.navigate(-1); });
  else {
    const back = screen.getByRole("button", { name: "Return to saved judgment" }); back.focus(); fireEvent.click(back);
  }
  await waitFor(() => expect(document.getElementById("signal-saved-source-5-77-2")).toHaveFocus());
  expect(outer).toHaveProperty("open", true); expect(nested).toHaveProperty("open", true);
  expect(screen.getByDisplayValue("First private draft")).toHaveValue("First private draft");
  expect(screen.getByDisplayValue("Second private draft")).toHaveValue("Second private draft");
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});

it("does not call an automatic criterion record an instructor's saved judgment on source return", async () => {
  const control = await setup({ open: false, saved: true, instructor: false, drafts: false });
  fireEvent.click(await screen.findByText("Recorded criterion results, 1 entry"));
  fireEvent.click(document.getElementById("signal-criterion-evidence-5-77")!);
  const action = document.getElementById("signal-saved-source-5-77-0")!; action.focus(); fireEvent.click(action);
  await act(async () => { control.release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  expect(screen.getByRole("button", { name: "Return to criterion record" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Return to saved judgment" })).not.toBeInTheDocument();
});

it.each(["original action", "missing action", "missing criterion"])("returns to the same saved criterion with %s, preserving unrelated drafts", async (mode) => {
  const control = await setup({ open: false, saved: true });
  const view = await screen.findByRole("button", { name: "View saved judgments (1)" }); view.focus(); fireEvent.click(view);
  await waitFor(() => expect(document.getElementById("criteria-results-heading")).toHaveFocus());
  fireEvent.click(document.getElementById("signal-criterion-evidence-5-77")!);
  const source = document.getElementById("signal-saved-source-5-77-0")!;
  source.focus(); fireEvent.click(source);
  await waitFor(() => expect(control.router.state.location.search).toContain("pane=manuscript"));
  await act(async () => { control.release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  if (mode !== "original action") await act(async () => {
    control.client.setQueryData(["report", 5], { ...REPORT, results: mode === "missing criterion" ? [] : [{ ...SAVED, evidence: [] }] });
  });
  const back = screen.getByRole("button", { name: "Return to saved judgment" }); back.focus(); fireEvent.click(back);
  const target = mode === "original action" ? "signal-saved-source-5-77-0"
    : mode === "missing action" ? "criterion-record-5-77" : "criteria-results-heading";
  await waitFor(() => expect(document.getElementById(target)).toHaveFocus());
  expect(control.router.state.location.search).toContain("criterion_record=77");
  expect(control.router.state.location.search).toContain("sentinel=kept");
  expect(control.drafts[0]).toHaveValue("First private draft");
  expect(control.drafts[1]).toHaveValue("Second private draft");
  if (mode === "missing criterion") expect(screen.getByText("That recorded criterion is not available in this report.")).toBeVisible();
});

it("keeps two private drafts and the full decision context through delayed source, return and history", async () => {
  const { router, scroll, release, drafts, action, fetch, client } = await setup();
  expect(router.state.location.search).toContain("view=decision");
  expect(router.state.location.search).toContain("sentinel=kept");
  expect(router.state.location.state).toEqual({ notice: "retained" });
  expect(document.getElementById("manuscript-panel")).toHaveFocus();
  scroll.mockClear();
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  expect(scroll).toHaveBeenCalledTimes(1);
  expect(document.getElementById("manuscript-panel")).toHaveFocus();
  await act(async () => { client.setQueryData(["unrelated"], "no focus"); });
  expect(scroll).toHaveBeenCalledTimes(1);
  const back = screen.getByRole("button", { name: "Return to criterion review" });
  back.focus(); fireEvent.click(back);
  await waitFor(() => expect(router.state.location.search).toBe(ORIGIN.slice(ORIGIN.indexOf("?"))));
  expect(action).toHaveFocus();
  expect(screen.getAllByRole("textbox")[0]).toBe(drafts[0]);
  expect(screen.getAllByRole("textbox")[1]).toBe(drafts[1]);
  expect(drafts[0]).toHaveValue("First private draft");
  expect(drafts[1]).toHaveValue("Second private draft");
  await act(async () => { await router.navigate(-1); await router.navigate(1); });
  expect(screen.getAllByRole("textbox")[0]).toBe(drafts[0]);
  expect(router.state.location.search).not.toContain("private");
  expect(JSON.stringify(router.state.location.state)).not.toContain("draft");
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it.each(["focus", "blur", "Tab", "PageDown", "wheel", "pointer", "query"])("does not reclaim source focus or scroll after %s intent", async (intent) => {
  const { router, scroll, release, drafts } = await setup();
  scroll.mockClear();
  if (intent === "focus") drafts[1].focus();
  if (intent === "blur") window.dispatchEvent(new Event("blur"));
  if (intent === "Tab" || intent === "PageDown") fireEvent.keyDown(window, { key: intent });
  if (intent === "wheel") fireEvent.wheel(window);
  if (intent === "pointer") fireEvent.pointerDown(document.body);
  if (intent === "query") await act(async () => { await router.navigate(router.state.location.pathname + router.state.location.search + "&changed=1"); });
  const focus = document.activeElement;
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  expect(document.activeElement).toBe(focus);
  expect(scroll).not.toHaveBeenCalled();
  expect(drafts[0]).toHaveValue("First private draft");
});

it("keeps the draft and an explicit return when the recorded source position is absent", async () => {
  const { release, drafts } = await setup();
  await act(async () => { release({ paragraphs: [], tables: [], images: [] }); });
  await screen.findByText("The recorded criterion anchor could not be placed");
  expect(screen.getByRole("button", { name: "Return to criterion review" })).toBeEnabled();
  expect(drafts[0]).toHaveValue("First private draft");
});

it("gives a repeated same-address action a fresh one-scroll attempt and retires it on new wheel intent", async () => {
  const { release, action, scroll, client, drafts } = await setup();
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  scroll.mockClear();
  action.focus(); fireEvent.click(action);
  await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
  expect(document.getElementById("manuscript-panel")).toHaveFocus();
  scroll.mockClear();
  action.focus(); fireEvent.click(action);
  fireEvent.wheel(window);
  await act(async () => { client.setQueryData(["report", 5, "document", "paragraphs"], { ...PARAGRAPHS, paragraphs: [...PARAGRAPHS.paragraphs] }); });
  // The explicit cached activation may place immediately; the later arrival cannot place again.
  expect(scroll.mock.calls.length).toBeLessThanOrEqual(1);
  expect(drafts[0]).toHaveValue("First private draft");
});

it("retires a repeated same-address attempt while its source is still delayed", async () => {
  const { release, action, scroll } = await setup();
  action.focus(); fireEvent.click(action);
  fireEvent.wheel(window);
  scroll.mockClear();
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  expect(scroll).not.toHaveBeenCalled();
});

it("places a new criterion target rather than consuming permission on the previously cached paragraph", async () => {
  const { release, scroll, drafts } = await setup();
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("A second source paragraph");
  scroll.mockClear();
  const action = screen.getByRole("link", { name: /Open in manuscript: Extracted paragraph 4\. Criterion 2/ });
  action.focus(); fireEvent.click(action);
  await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
  expect((scroll.mock.instances[0] as unknown as HTMLElement).dataset.sourceParagraph).toBe("3");
  expect(drafts[0]).toHaveValue("First private draft");
  expect(drafts[1]).toHaveValue("Second private draft");
});

it("preserves ordinary direct decision-anchor placement without a local inspection attempt", async () => {
  const { release } = await setup({ origin: ORIGIN.replace("pane=review", "pane=manuscript") + "&anchor=paragraph+2", open: false, drafts: false });
  await act(async () => { release(PARAGRAPHS); });
  await waitFor(() => expect(screen.getByText("The requested source paragraph")).toHaveFocus());
});

it("retires inspection mode across a different run before returning to an ordinary direct anchor", async () => {
  const { release, router } = await setup();
  await act(async () => { release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  await act(async () => { await router.navigate("/report/6/document?view=decision&pane=manuscript"); });
  fireEvent.click(await screen.findByRole("button", { name: "Leave without saving" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/report/6/document"));
  await act(async () => { await router.navigate("/report/5/document?view=decision&pane=manuscript&anchor=paragraph+2"); });
  await waitFor(() => expect(screen.getByText("The requested source paragraph")).toHaveFocus());
});

it.each(["explicit return", "browser Back"])("full report restores the exact saved source action through %s", async (mode) => {
  const control = await setup({ origin: "/report/5?criterion_record=all&sentinel=kept", full: true, open: false, saved: true });
  fireEvent.click(document.getElementById("signal-criterion-evidence-5-77")!);
  const source = document.getElementById("signal-saved-source-5-77-0")!; source.focus(); fireEvent.click(source);
  await act(async () => { control.release(PARAGRAPHS); });
  await screen.findByText("The requested source paragraph");
  const back = screen.getByRole("link", { name: "Return to saved judgment" });
  expect(back).toHaveAttribute("href", "/report/5?criterion_record=77&sentinel=kept#signal-saved-source-5-77-0");
  if (mode === "explicit return") { back.focus(); fireEvent.click(back); }
  else await act(async () => { await control.router.navigate(-1); });
  await waitFor(() => expect(document.getElementById("signal-saved-source-5-77-0")).toHaveFocus());
  expect(document.getElementById("signal-saved-source-5-77-0")?.closest("details")).toHaveProperty("open", true);
  expect(control.router.state.location.search).toContain("sentinel=kept");
  expect(control.router.state.location.search).toContain("criterion_record=77");
  expect(control.router.state.location.state).toEqual({ notice: "retained" });
  expect(screen.getByDisplayValue("First private draft")).toBeVisible();
  expect(screen.getByDisplayValue("Second private draft")).toBeVisible();
});

const FULL_ORIGIN = "/report/5?sentinel=kept&filter=all&source=page_text&text_page=4&page=3&finding=9&criterion_record=all";
const EXACT_REASON = "  Check this requirement carefully.\n  Keep this spacing.  ";
it.each(["source", "coverage"].flatMap(prefix => ["explicit Return", "Back"].map(mode => ({ prefix, mode }))))(
  "full report $prefix preserves the exact draft, query and focus through $mode", async ({ prefix, mode }) => {
    const control = await setup({ origin: FULL_ORIGIN, full: true, open: false, coverage: COVERAGE });
    fireEvent.change(control.drafts[0], { target: { value: EXACT_REASON } });
    const actionId = `signal-resolution-${prefix}-5-1-${prefix === "coverage" ? 1 : 0}`;
    const source = document.getElementById(actionId)!;
    expect(source).not.toBeNull(); source.focus(); fireEvent.click(source);
    await waitFor(() => expect(control.router.state.location.pathname).toBe("/report/5/document"));
    const params = new URLSearchParams(control.router.state.location.search);
    expect(params.get("view")).toBe("decision"); expect(params.get("pane")).toBe("manuscript");
    expect(params.get("sentinel")).toBe("kept"); expect(params.get("filter")).toBe("all");
    for (const key of ["source", "text_page", "page", "finding", "criterion_record"]) expect(params.has(key)).toBe(false);
    expect(JSON.stringify(control.router.state.location.state)).not.toContain(EXACT_REASON);
    expect(screen.getByText("Source opened from criterion review")).toBeVisible();
    const back = await screen.findByRole("link", { name: "Return to criterion review: Criterion 1" });
    expect(back).toHaveAttribute("href", `${FULL_ORIGIN}#${actionId}`);
    // The return exists while the source itself is still loading.
    if (mode === "explicit Return") { back.focus(); fireEvent.click(back); }
    else await act(async () => { await control.router.navigate(-1); });
    await waitFor(() => expect(document.getElementById(actionId)).toHaveFocus());
    expect(control.router.state.location.search).toBe(FULL_ORIGIN.slice(FULL_ORIGIN.indexOf("?")));
    expect(control.router.state.location.state).toEqual({ notice: "retained" });
    expect(screen.getAllByRole("textbox")[0]).toHaveValue(EXACT_REASON);
    expect(screen.getByDisplayValue("Second private draft")).toBeVisible();
    expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    await act(async () => { control.release(PARAGRAPHS); });
  });

it.each(["missing passage", "missing result"])("full-report Return provides a truthful fallback for %s", async mode => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true });
  const items = mode === "missing passage" ? [{ ...ITEMS[0], evidence: [] }, ITEMS[1]] : [ITEMS[1]];
  control.setPendingReply(async () => new Response(JSON.stringify(items), { status: 200 }));
  await act(async () => { await control.client.invalidateQueries({ queryKey: ["report", 5, "escalated"], exact: true }); });
  const back = screen.getByRole("link", { name: /Return to criterion review/ }); back.focus(); fireEvent.click(back);
  const fallback = "signal-resolution-heading-1";
  // An unresolved private draft remains as a retained entry if its result disappeared.
  await waitFor(() => expect(document.getElementById(fallback)).toHaveFocus());
  expect(screen.getByText("The original source action is no longer available. Review the current report below.")).toBeVisible();
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  await act(async () => { control.release(PARAGRAPHS); });
});

it("does not infer an absent action from a failed review read and restores it after Retry", async () => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true, drafts: false });
  await act(async () => { control.client.setQueryData(["report", 5, "escalated"], []); });
  control.setPendingReply(async () => new Response(JSON.stringify({ code: "internal_error", message: "Unavailable" }), { status: 500 }));
  await act(async () => { await control.client.invalidateQueries({ queryKey: ["report", 5, "escalated"], exact: true }); });
  const back = screen.getByRole("link", { name: "Return to criterion review" }); back.focus(); fireEvent.click(back);
  const retry = await screen.findByRole("button", { name: "Retry review items" });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 600)); });
  expect(screen.queryByText("The original source action is no longer available. Review the current report below.")).not.toBeInTheDocument();
  control.setPendingReply(async () => new Response(JSON.stringify(ITEMS), { status: 200 }));
  // Retry is new intent: the late action must not steal focus from this button.
  retry.focus(); fireEvent.click(retry);
  await screen.findByRole("link", { name: /Open in manuscript: .*Criterion 1/ });
  expect(document.getElementById("signal-resolution-source-5-1-0")).not.toHaveFocus();
  await act(async () => { control.release(PARAGRAPHS); });
});

it("a new workspace source replaces the full-report origin without competing Return controls", async () => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true });
  const other = document.getElementById("signal-resolution-source-5-2-0")!;
  other.focus(); fireEvent.click(other);
  await waitFor(() => expect(control.router.state.location.state).toEqual({ notice: "retained" }));
  expect(screen.queryByText("Source opened from criterion review")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Return to criterion review" })).toHaveLength(1);
  await act(async () => { control.release(PARAGRAPHS); });
});

it.each(["none", "wheel", "focus"])("waits beyond the ordinary timeout for review data and respects newer %s intent", async intent => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true, drafts: false });
  await act(async () => { control.client.setQueryData(["report", 5, "escalated"], []); });
  let deliver!: (response: Response) => void;
  const held = new Promise<Response>(resolve => { deliver = resolve; });
  control.setPendingReply(() => held);
  await act(async () => { void control.client.invalidateQueries({ queryKey: ["report", 5, "escalated"], exact: true }); });
  const back = screen.getByRole("link", { name: "Return to criterion review" }); back.focus(); fireEvent.click(back);
  await waitFor(() => expect(control.router.state.location.pathname).toBe("/report/5"));
  if (intent === "wheel") fireEvent.wheel(window);
  const other = screen.getByRole("button", { name: "Share report" });
  if (intent === "focus") other.focus();
  const previousFocus = document.activeElement;
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 600)); });
  expect(document.activeElement).toBe(previousFocus);
  expect(screen.queryByText("The original source action is no longer available. Review the current report below.")).not.toBeInTheDocument();
  await act(async () => { deliver(new Response(JSON.stringify(ITEMS), { status: 200 })); });
  const action = await screen.findByRole("link", { name: /Open in manuscript: .*Criterion 1/ });
  if (intent === "none") await waitFor(() => expect(action).toHaveFocus());
  else expect(action).not.toHaveFocus();
  if (intent === "focus") expect(other).toHaveFocus();
  await act(async () => { control.release(PARAGRAPHS); });
});

it.each([{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }])("leaves modified full-report source activation to the browser: %o", async modifiers => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true, open: false });
  const before = control.router.state.location;
  // React has already handled the modified activation at its root. Prevent
  // jsdom's unsupported new-document default at the outer document boundary.
  const preventNativeNavigation = (event: Event) => event.preventDefault();
  document.addEventListener("click", preventNativeNavigation);
  try { fireEvent.click(control.action, modifiers); }
  finally { document.removeEventListener("click", preventNativeNavigation); }
  expect(control.router.state.location).toBe(before);
  expect(screen.getByDisplayValue("First private draft")).toBeVisible();
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  await act(async () => { control.release(PARAGRAPHS); });
});

it.each(["explicit Return", "Back", "pane switch"])("full-report source retains return focus after actual Page text query changes through %s", async mode => {
  const control = await setup({ origin: "/report/5?sentinel=kept", full: true, pdf: true });
  const read = await screen.findByRole("button", { name: "Read text" }); read.focus(); fireEvent.click(read);
  await screen.findByText("Saved source text page 3");
  expect(control.router.state.location.search).toContain("source=page_text");
  const next = screen.getByRole("button", { name: "Next page" }); next.focus(); fireEvent.click(next);
  await screen.findByText("Saved source text page 4");
  if (mode === "pane switch") {
    const review = screen.getByRole("tab", { name: /^Review\./ }); review.focus(); fireEvent.click(review);
    const manuscript = screen.getByRole("tab", { name: "Manuscript" }); manuscript.focus(); fireEvent.click(manuscript);
  }
  if (mode !== "Back") {
    const back = screen.getByRole("link", { name: /Return to criterion review/ }); back.focus(); fireEvent.click(back);
  } else await act(async () => { await control.router.navigate(-1); await control.router.navigate(-1); await control.router.navigate(-1); });
  await waitFor(() => expect(document.getElementById("signal-resolution-source-5-1-0")).toHaveFocus());
  expect(control.router.state.location.pathname).toBe("/report/5");
  expect(control.router.state.location.search).toBe("?sentinel=kept");
  expect(screen.getByDisplayValue("First private draft")).toBeVisible();
  expect(control.fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  await act(async () => { control.release(PARAGRAPHS); });
});
