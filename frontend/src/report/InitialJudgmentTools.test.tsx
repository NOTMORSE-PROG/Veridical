import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Link, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JudgmentContext, JudgmentHistoryEntry, JudgmentRecordInput, JudgmentSnapshot } from "../api/judgments";
import type { CriterionResultOut, ReportOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { renderWithReviewProviders } from "../test/renderWithProviders";
import { SignalCriteriaResults } from "./SignalReviewSections";
import { useReport } from "./useReport";

const REASON = "  I inspected the requirement\nand the source before recording this judgment.  ";
const original: JudgmentContext["original_assessment"] = { kind: "semantic", basis: "llm", reason: null,
  reasoning: "The original saved explanation.", ai_majority_verdict: "pass", evidence: [], unverified_evidence: [],
  support_withheld: false, support_status: null, evidence_unavailable: false, original_outcome: "passed",
  original_score: 100, original_level: null };

function Fixture({ capture }: { capture: (client: QueryClient) => void }) {
  capture(useQueryClient());
  const report = useReport(5);
  const location = useLocation();
  return location.pathname.endsWith("/document")
    ? <><p>Controlled source passage</p><Link to="/report/5">Return to criterion</Link></>
    : <><SignalCriteriaResults checkRunId={5} results={report.data?.results ?? []} />
      <Link to="/report/5/document">Inspect source</Link></>;
}

function setup() {
  let epoch = 0;
  let manual: JudgmentSnapshot | null = null;
  let mode: "ok" | "unknown" | "conflict" | "malformed" = "ok";
  const posts: JudgmentRecordInput[] = [];
  let entries: JudgmentHistoryEntry[] = [];
  const row = (): CriterionResultOut => ({ criterion_id: 101, check_result_id: 41, judgment_revision: manual?.judgment_revision ?? null,
    text: "Explain the original manuscript evidence", type: "semantic", kind: "semantic", weight: 1, weight_importance: "med",
    outcome: manual?.outcome ?? "passed", score: manual ? manual.score : 100, basis: "llm", anchor: null,
    reason: null, reasoning: original.reasoning, evidence: [], level: manual?.level ?? null,
    resolution: manual ? { type: manual.resolution_type, reason: manual.reason, ai_majority_verdict: "pass" } : null });
  const report = () => ({ check_run_id: 5, assessment_revision: epoch, decision: null, results: [row()] }) as ReportOut;
  const context = (): JudgmentContext => ({ check_run_id: 5, criterion_id: 101, check_result_id: 41,
    criterion_text: row().text, assessment_revision: epoch, decision: null, can_revise: manual !== null,
    can_record: manual === null, unavailable_reason: null, current: manual,
    choices: { kind: "pass_fail", levels: [], exclusion_allowed: true, unavailable_reason: null },
    original_assessment: original });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
    if (path.endsWith("/escalated")) return json([]);
    if (path.endsWith("/report")) return json(report());
    if (path.endsWith("/judgment")) return json(context());
    if (path.endsWith("/judgment/history")) return json({ check_run_id: 5, criterion_id: 101, check_result_id: 41,
      assessment_revision: epoch, judgment_revision: manual!.judgment_revision, current: manual,
      entries, history_incomplete: false, next_cursor: null });
    if (path.endsWith("/judgment/records")) {
      const body = JSON.parse(init!.body as string) as JudgmentRecordInput;
      posts.push(body);
      if (mode === "unknown") throw new TypeError("Controlled unconfirmed save");
      if (mode === "conflict") { epoch += 1; return json({ error: { code: "conflict", message: "Controlled stale epoch" } }, 409); }
      manual = { judgment_revision: 1, resolution_type: body.resolution, reason: body.reason,
        actor_id: 1, recorded_at: "2026-10-25T15:00:00Z", provenance: "recorded", level: null,
        outcome: body.resolution === "needs_document" ? "not_applicable" : body.resolution === "mark_fail" ? "failed" : "passed",
        score: body.resolution === "needs_document" ? null : body.resolution === "mark_fail" ? 0 : 100 };
      epoch += 1;
      const entry: JudgmentHistoryEntry = { schema_version: 2, entry_id: 21, check_run_id: 5, criterion_id: 101,
        check_result_id: 41, action: "judgment_recorded", assessment_revision_before: epoch - 1,
        assessment_revision_after: epoch, before: null, after: manual, performed_by: 1,
        performed_at: manual.recorded_at!, original_assessment: original };
      entries = [entry];
      return json({ context: context(), report: report(), entry: mode === "malformed" ? { ...entry, before: manual } : entry });
    }
    return json({ error: { code: "not_found", message: "Unconfigured test path" } }, 404);
  }));
  let client!: QueryClient;
  renderWithReviewProviders(<Fixture capture={value => { client = value; }} />, { reviewRunId: 5, route: "/report/5" });
  return { posts, mode: (value: typeof mode) => { mode = value; }, client: () => client };
}

async function open() {
  fireEvent.click(await screen.findByRole("button", { name: /^Record instructor judgment:/ }));
  return await screen.findByLabelText("Reason for your judgment (required)");
}
function propose(choice = "Meets criterion") {
  fireEvent.click(screen.getByRole("radio", { name: choice }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: REASON } });
  fireEvent.click(screen.getByRole("button", { name: "Review judgment" }));
}

describe("Initial instructor judgment over a settled assessment", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("starts blank, requires a choice and reason, and permits an explicit same-grade affirmation", async () => {
    const control = setup();
    await open();
    expect(screen.getAllByRole("radio").every(radio => !(radio as HTMLInputElement).checked)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Review judgment" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose your judgment");
    expect(control.posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("radio", { name: "Meets criterion" }));
    fireEvent.click(screen.getByRole("button", { name: "Review judgment" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reason for your judgment");
    propose();
    const dialog = screen.getByRole("dialog", { name: "Record your judgment?" });
    expect(dialog).toHaveTextContent("Automated assessment");
    expect(dialog).not.toHaveTextContent("Current judgment");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save judgment" }));
    expect(await screen.findByRole("heading", { name: "Judgment recorded" })).toBeInTheDocument();
    expect(control.posts).toEqual([{ expected_revision: 0, resolution: "mark_pass", reason: REASON.trim() }]);
    expect(screen.getByRole("button", { name: /^Revise recorded judgment:/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Judgment history:/ }));
    expect(await screen.findByText("Initial recorded judgment: Current at time of this history read")).toBeInTheDocument();
    const originalSummary = screen.getByText("Original automated assessment");
    fireEvent.click(originalSummary);
    expect(within(originalSummary.closest("details")!).getByText("The original saved explanation.")).toBeVisible();
    expect(screen.queryByText("Previous judgment")).not.toBeInTheDocument();
  });

  it("keeps exact unsaved input through a source round trip", async () => {
    setup();
    const reason = await open();
    fireEvent.change(reason, { target: { value: REASON } });
    fireEvent.click(screen.getByRole("radio", { name: "Does not meet" }));
    fireEvent.click(screen.getByRole("link", { name: "Inspect source" }));
    await screen.findByText("Controlled source passage");
    fireEvent.click(screen.getByRole("link", { name: "Return to criterion" }));
    expect(await screen.findByRole("textbox")).toHaveValue(REASON);
    expect(screen.getByRole("radio", { name: "Does not meet" })).toBeChecked();
  });

  it.each(["unknown", "malformed"] as const)("retains an unconfirmed %s save and never automatically posts again", async mode => {
    const control = setup(); control.mode(mode);
    await open(); propose("Does not meet");
    fireEvent.click(screen.getByRole("button", { name: "Save judgment" }));
    await screen.findByRole("heading", { name: "Judgment save is unconfirmed" });
    expect(screen.getByRole("textbox")).toHaveValue(REASON);
    expect(screen.getByRole("button", { name: "Review judgment" })).toBeDisabled();
    await act(async () => { await control.client().invalidateQueries({ queryKey: ["report", 5] }); });
    expect(control.posts).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Stop editing this judgment" })).toBeEnabled();
  });

  it("requires an explicit current-assessment review after a stale refusal", async () => {
    const control = setup(); control.mode("conflict");
    await open(); propose();
    fireEvent.click(screen.getByRole("button", { name: "Save judgment" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Review judgment" })).toBeDisabled());
    expect(screen.getByRole("textbox")).toHaveValue(REASON);
    fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use this draft with the current assessment" }));
    expect(screen.getByRole("button", { name: "Review judgment" })).toBeEnabled();
    expect(control.posts).toHaveLength(1);
  });

  it("refuses a pending confirmation after its authenticated owner changes", async () => {
    const control = setup(); await open(); propose();
    await act(async () => { control.client().setQueryData(ME_QUERY_KEY, { id: 99 }); });
    fireEvent.click(screen.getByRole("button", { name: "Save judgment" }));
    expect(control.posts).toHaveLength(0);
  });
});
