import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut, FlagSummaryOut } from "../api/types";
import { renderWithReviewProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";

vi.mock("./PdfPane", () => ({ PdfPane: () => <div>PDF source</div> }));
vi.mock("./ReuseExplorePanel", () => ({ ReuseExplorePanel: () => null }));
const RECORDS: FlagSummaryOut[] = Array.from({ length: 31 }, (_, i) => ({ id: i + 39,
  check_kind: "originality_reuse", affects_readiness: false, severity: "high", criterion_text: null,
  evidence_excerpt: "Same saved excerpt", page_anchor: "page 2", overridden: i === 2,
  is_passage_level: true, first_upload_context: false, confirmed_citation_source: false,
  problem_kind: "reuse_high_similarity_passage", matched_ref: 13,
}));
const REPORT = { check_run_id: 5, manuscript_id: 10, manuscript_group_label: "Local comparison control",
  manuscript_original_filename: "source.pdf", rubric_title: "Custom format", status: "needs_review",
  composite_score: null, thresholds: {}, reason: null, flag_deduction: 0, unresolved_high_flag_count: 0,
  decision: null, decided_at: null, decision_note: null, pending_review_count: 5, rubric_is_current: true,
  llm_mode: "real", rubric_needs_review: false, rubric_parse_issues: null, previous_status: null,
  previous_composite_score: null, integrity_check_status: [], results: [] };
function setup(query = "", delay = false, report = REPORT, withPair = false) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  let release!: (response: Response) => void;
  const held = new Promise<Response>((resolve) => { release = resolve; });
  const fallback = stubFetchByPath({
    "/check-runs/5/document": { manuscript_id: 10, source_format: "pdf", available: true, original_filename: "source.pdf", page_count: 4, regions: [] },
    "/check-runs/5/report": report, "/check-runs/5/flags": [...RECORDS].reverse(), "/check-runs/5/escalated": [],
    "/check-runs/5/document/reuse-matches": { matches: [], passage_archive_size_n: 0 },
  });
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const match = path.match(/^\/flags\/(\d+)$/);
    if (match) {
      if (delay) return await held;
      return response(Number(match[1]), withPair);
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  let client!: QueryClient;
  function ClientProbe() { client = useQueryClient(); return null; }
  renderWithReviewProviders(<><ClientProbe /><button>Outside control</button><SignalDocumentViewerPage /></>, { route: `/report/5/document?pane=review${query}`, path: "/report/:checkRunId/document" });
  return { fetch, release, client };
}
function response(id: number, withPair = false) {
  const flag = { ...RECORDS.find((record) => record.id === id), id, check_result_id: 9, check_run_id: 5,
    manuscript_group_label: "Local comparison control", confidence: null, annotation: id === 41 ? "Retained annotation" : null,
    override_reason: null, ai_verdict_summary: "reuse_high_similarity_passage", ai_reasoning: "Actual stored reasoning",
    llm_mode: "real", passage_pair: withPair ? { own_excerpt: "Stored central manuscript excerpt.", matched_excerpt: "Stored central comparison excerpt.", own_context_before: "Attributed to Author A (2022).", own_context_after: "Author B (2023) adds context.", matched_context_before: "Attributed to Author C (2020).", matched_context_after: "Author D (2021) follows.", matched_ref: 13, context_words_each_side: 60, level: "high_similarity", similarity: 0.9 } : null, evidence_unavailable: false } as FlagOut;
  return new Response(JSON.stringify(flag), { status: 200 });
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("optional comparison workspace navigation", () => {
  it.each([401, 403, 404, 503])("does not claim a selected record disappeared after a failed collection read (%s)", async status => {
    const c = setup("&view=finding&finding=41", false, REPORT, true);
    await screen.findByRole("button", { name: "Review in full width" });
    const base = c.fetch.getMockImplementation()!;
    c.fetch.mockImplementation(async (input, init) => new URL(String(input), "http://localhost").pathname === "/check-runs/5/flags"
      ? new Response(JSON.stringify({ error: { code: "controlled", message: "Controlled failed collection read" } }), { status }) : base(input, init));
    await act(async () => { await c.client.invalidateQueries({ queryKey: ["report", 5, "flags"], exact: true }); });
    if (status === 503) {
      await screen.findByRole("button", { name: "Retry comparison list" });
      expect(document.querySelector(".passage-pair")).toBeInTheDocument();
    } else await waitFor(() => expect(document.querySelector(".passage-pair")).not.toBeInTheDocument());
    expect(screen.queryByText("That finding is no longer available")).not.toBeInTheDocument();
  });
  it("reports absence only after a successful collection read omits the selected record", async () => {
    const c = setup("&view=finding&finding=41", false, REPORT, true);
    await screen.findByRole("button", { name: "Review in full width" });
    await act(async () => { c.client.setQueryData(["report", 5, "flags"], RECORDS.filter(record => record.id !== 41)); });
    expect(await screen.findByText("That finding is no longer available")).toBeVisible();
  });
  it("withholds an older absence claim while the collection is being refreshed", async () => {
    const c = setup("&view=finding&finding=41", false, REPORT, true);
    await screen.findByRole("button", { name: "Review in full width" });
    await act(async () => { c.client.setQueryData(["report", 5, "flags"], RECORDS.filter(record => record.id !== 41)); });
    await screen.findByText("That finding is no longer available");
    let release!: (response: Response) => void;
    const held = new Promise<Response>(resolve => { release = resolve; });
    const base = c.fetch.getMockImplementation()!;
    c.fetch.mockImplementation((input, init) => new URL(String(input), "http://localhost").pathname === "/check-runs/5/flags" ? held : base(input, init));
    let refresh!: Promise<void>;
    act(() => { refresh = c.client.invalidateQueries({ queryKey: ["report", 5, "flags"], exact: true }); });
    await waitFor(() => expect(screen.queryByText("That finding is no longer available")).not.toBeInTheDocument());
    await act(async () => { release(new Response(JSON.stringify(RECORDS), { status: 200 })); await refresh; });
    expect(await screen.findByRole("button", { name: "Review in full width" })).toBeInTheDocument();
    expect(screen.queryByText("That finding is no longer available")).not.toBeInTheDocument();
  });
  it("keeps evidence focus when a retained comparison-list Retry disappears after recovery", async () => {
    const c = setup("&view=finding&finding=41", false, REPORT, true);
    const base = c.fetch.getMockImplementation()!;
    let fail = false;
    c.fetch.mockImplementation(async (input, init) => new URL(String(input), "http://localhost").pathname === "/check-runs/5/flags" && fail
      ? new Response(JSON.stringify({ error: { code: "internal", message: "Controlled unavailable read" } }), { status: 503 }) : base(input, init));
    const expand = await screen.findByRole("button", { name: "Review in full width" });
    const inspector = document.getElementById("review-panel")!; inspector.scrollTo = vi.fn();
    expand.focus(); fireEvent.click(expand);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Evidence to compare or check" })).toHaveFocus());
    fail = true;
    await act(async () => { await c.client.invalidateQueries({ queryKey: ["report", 5, "flags"], exact: true }); });
    const retry = await screen.findByRole("button", { name: "Retry comparison list" });
    fail = false; retry.focus(); fireEvent.click(retry);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry comparison list" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Evidence to compare or check" })).toHaveFocus();
    expect(document.querySelector(".signal-document-workbench")).toHaveAttribute("data-reading", "full");
  });
  it("expands at the actual evidence and returns to its exact action without replacing the source or article", async () => {
    const { fetch } = setup("&view=finding&finding=41", false, REPORT, true);
    const expand = await screen.findByRole("button", { name: "Review in full width" });
    const article = document.querySelector(".signal-document-selected")!;
    const source = document.getElementById("manuscript-panel")!;
    const inspector = document.getElementById("review-panel")!;
    inspector.scrollTo = vi.fn();
    expand.focus(); fireEvent.click(expand);
    const evidence = screen.getByRole("heading", { name: "Evidence to compare or check" });
    await waitFor(() => expect(evidence).toHaveFocus());
    expect(inspector.scrollTo).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".signal-document-workbench")).toHaveAttribute("data-reading", "full");
    expect(document.querySelector(".signal-document-selected")).toBe(article);
    expect(document.getElementById("manuscript-panel")).toBe(source);
    for (const text of ["Attributed to Author A (2022).", "Author B (2023) adds context.", "Attributed to Author C (2020).", "Author D (2021) follows."]) expect(screen.getByText(text)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Return to split review" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Review in full width" })).toHaveFocus());
    expect(document.querySelector(".signal-document-selected")).toBe(article);
    expect(document.getElementById("manuscript-panel")).toBe(source);
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
  it("places View review after its narrow pane commits, without relying on an early animation frame", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const realFocus = HTMLElement.prototype.focus;
    vi.spyOn(HTMLElement.prototype, "focus").mockImplementation(function (this: HTMLElement, options) {
      if (this.closest("[data-active='false']")) return;
      realFocus.call(this, options);
    });
    setup("&view=finding&finding=69");
    await screen.findByText("Actual stored reasoning");
    fireEvent.click(screen.getByRole("tab", { name: "Manuscript" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Manuscript" })).toHaveAttribute("aria-selected", "true"));
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => { callback(performance.now()); return 0; });
    const action = screen.getByRole("button", { name: "View review" }); action.focus(); fireEvent.click(action);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High passage similarity" })).toHaveFocus());
    expect(screen.getByText("Comparison lead 31 of 31. Record #69.")).toBeVisible();
  });
  it("starts comparison-only review with the actual evidence index and no empty queue scaffolding", async () => {
    setup("", false, { ...REPORT, pending_review_count: 0 });
    const heading = await screen.findByRole("heading", { name: "Comparison leads", level: 2 });
    const section = heading.closest("section")!;
    expect(section.parentElement?.firstElementChild).toBe(section);
    expect(screen.queryByText("Your review tasks")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Review queue" })).not.toBeInTheDocument();
    expect(screen.queryByText(/0 other findings|0 resolved|No integrity findings/)).not.toBeInTheDocument();
    const about = screen.getByRole("button", { name: "About leads" });
    expect(about.parentElement).toContainElement(screen.getByRole("group", { name: "Filter comparison leads" }));
    expect(about).toHaveAttribute("aria-expanded", "false");
    about.focus(); fireEvent.click(about);
    expect(about).toHaveFocus();
    expect(about).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/These are optional saved comparison records/)).toBeVisible();
    fireEvent.click(about);
    expect(screen.getByText(/These are optional saved comparison records/)).not.toBeVisible();
  });
  it("keeps five required judgments before 31 optional records without a combined task count", async () => {
    setup();
    const criteria = await screen.findByRole("heading", { name: "5 criteria need your judgment" });
    const leads = screen.getByRole("heading", { name: "Comparison leads" });
    expect(criteria.compareDocumentPosition(leads) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("31 records")).toBeVisible();
    const tab = screen.getByRole("tab", { name: /Review\. 5 criteria awaiting judgment\. 31 optional comparison leads/ });
    expect(tab).toHaveTextContent(/^Review$/); expect(tab).not.toHaveAccessibleName(/36|tasks/);
    fireEvent.click(screen.getByRole("button", { name: /^Review evidence for Comparison lead 3 of 31\. Record #41\./ }));
    await screen.findByText("Actual stored reasoning");
    fireEvent.click(screen.getByText("Recorded reasoning and technical details"));
    expect(await screen.findByText("Actual stored reasoning")).toBeVisible();
    expect(screen.getByRole("link", { name: "Review instructor actions for this location" })).toHaveAttribute("href", "/flags/41");
    expect(screen.queryByText("Finding 1 of 31")).not.toBeInTheDocument();
  });
  it("retains an exact selected record outside its filter rather than selecting an arbitrary neighbor", async () => {
    setup("&view=finding&finding=41&lead_view=open");
    expect(await screen.findByText("This comparison lead is outside the current filter.")).toBeVisible();
    expect(screen.getByText("Comparison lead 3 of 31. Record #41.")).toBeVisible();
    for (const name of ["Previous comparison lead", "Next comparison lead"]) expect(screen.getByRole("button", { name })).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText(/Item 0/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all comparison leads" }));
    const next = screen.getByRole("button", { name: "Next comparison lead" }); next.focus(); fireEvent.click(next);
    await waitFor(() => expect(screen.getByText("Comparison lead 4 of 31. Record #42.")).toBeVisible());
    expect(screen.getByRole("heading", { name: "High passage similarity" })).toHaveFocus();
  });
  it("returns a record beyond the first batch to its exact row with stable ordinal", async () => {
    setup("&view=finding&finding=69");
    await screen.findByText("Actual stored reasoning");
    const back = screen.getByRole("button", { name: "Back to review queue" }); back.focus(); fireEvent.click(back);
    const row = await screen.findByRole("button", { name: /^Review evidence for Comparison lead 31 of 31\. Record #69\./ });
    expect(row).toHaveFocus();
    expect(screen.getAllByRole("button", { name: /Review evidence for Comparison lead/ })).toHaveLength(7);
  });
  it("keeps the detail heading connected through delay/failure without stealing later focus", async () => {
    const { release } = setup("&view=finding&finding=41", true);
    const heading = await screen.findByRole("heading", { name: "High passage similarity" });
    expect(await screen.findByText("Loading comparison lead record.")).toBeVisible();
    const outside = screen.getByRole("button", { name: "Outside control" }); outside.focus();
    await act(async () => { release(new Response(JSON.stringify({ error: { code: "internal", message: "Controlled failure" } }), { status: 503 })); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Comparison lead details could not be loaded");
    expect(screen.getByRole("heading", { name: "High passage similarity" })).toBe(heading);
    expect(outside).toHaveFocus();
  });
});
