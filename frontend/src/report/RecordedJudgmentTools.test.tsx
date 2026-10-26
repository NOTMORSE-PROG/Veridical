import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { RequireAuth } from "../auth/RequireAuth";
import { CriterionReviewLayout } from "./CriterionReviewScope";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Link, createMemoryRouter, RouterProvider, useLocation } from "react-router";
import type { JudgmentContext, JudgmentHistoryEntry, JudgmentRevisionInput, JudgmentSnapshot } from "../api/judgments";
import type { CriterionResultOut, ReportOut } from "../api/types";
import { renderWithReviewProviders } from "../test/renderWithProviders";
import { SignalCriteriaResults } from "./SignalReviewSections";
import { useReport } from "./useReport";
import { useCriterionReviewScope } from "./criterionReviewContext";

const OLD_REASON = "I checked the original manuscript evidence.";
const NEW_REASON = "The later source passage supports a different recorded judgment.";
const initial: JudgmentSnapshot = { judgment_revision: 1, resolution_type: "mark_pass", outcome: "passed", score: 100,
  level: null, reason: OLD_REASON, actor_id: 1, recorded_at: "2026-10-23T00:00:00+00:00", provenance: "recorded" };

function Fixture({ captureClient }: { captureClient: (client: QueryClient) => void }) {
  captureClient(useQueryClient());
  const report = useReport(5);
  const location = useLocation();
  const review = useCriterionReviewScope(5);
  return <><SignalCriteriaResults checkRunId={5} results={report.data?.results ?? []} /><button>Another task</button>
    <Link to="/elsewhere">Leave review</Link><p data-testid="path">{location.pathname}</p>
    <button onClick={() => {
      review.choose({ check_result_id: 42, criterion_id: 102, criterion_text: "Another unresolved criterion", weight: 1,
        agreement: null, votes: [], ai_majority_verdict: null, reason: null, review_reason: "low_confidence", unverified_evidence: null }, "mark_pass");
      review.changeReason(42, NEW_REASON);
      review.confirm(42, () => "Controlled acknowledgment");
    }}>Start controlled pending save</button>
    <button onClick={() => review.revisions.open(101, "current")}>Change captured revision draft</button>
  </>;
}
function setup(options: { levelled?: boolean; legacy?: boolean; actualAuthRoute?: boolean; blankComment?: boolean } = {}) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  let epoch = 1;
  let current = structuredClone(initial);
  if (options.blankComment) current.reason = "";
  if (options.levelled) current = { ...current, resolution_type: "mark_level", score: 0,
    level: { ordinal: 0, name: "No evidence", points: 0, max_points: 3 } };
  if (options.legacy) current = { ...current, judgment_revision: 0, resolution_type: "mark_level", score: 33.33,
    level: null, actor_id: null, recorded_at: null, provenance: "legacy_partial" };
  const row = (): CriterionResultOut => ({ check_result_id: 41, judgment_revision: current.judgment_revision,
    criterion_id: 101, text: "The manuscript explains its evidence", type: "semantic", kind: "semantic",
    weight: 1, weight_importance: "med", outcome: current.outcome, score: current.score, basis: "llm", anchor: "page 3",
    reasoning: "Original system explanation.", reason: "Original uncertainty.", evidence: [{ anchor: "page 3", quote: "Actual source excerpt" }],
    level: current.level, resolution: { type: current.resolution_type, reason: current.reason, ai_majority_verdict: "pass" } });
  const report = () => ({ check_run_id: 5, assessment_revision: epoch, decision: null, results: [row()] }) as ReportOut;
  const context = (): JudgmentContext => ({ check_run_id: 5, criterion_id: 101, check_result_id: 41,
    criterion_text: row().text, assessment_revision: epoch, decision: null, can_revise: true, unavailable_reason: null,
    current, choices: { kind: options.levelled || options.legacy ? "levelled" : "pass_fail", exclusion_allowed: true,
      unavailable_reason: null, levels: options.levelled || options.legacy ? [
        { level: 0, name: "No evidence", points: 0, descriptor: "No supported evidence" },
        { level: 1, name: "Different zero grade", points: 0, descriptor: "A different academic judgment" },
        { level: 2, name: "Supported", points: 3, descriptor: "Full support" },
      ] : [] }, original_assessment: { kind: "semantic", basis: "llm", reason: "Original uncertainty.", reasoning: "Original system explanation.",
      ai_majority_verdict: "pass", evidence: [], unverified_evidence: [], support_withheld: false,
      support_status: null, evidence_unavailable: false, original_outcome: null, original_score: null, original_level: null } });
  const posts: JudgmentRevisionInput[] = [];
  let response: "ok" | "conflict" | "unknown" | "malformed" | "wrong_result" | "missing" = "ok";
  let wrongReadResult = false;
  let failRead = false;
  let readStatus = 503;
  let historyIncomplete = false;
  let failHistory = false;
  let holdRead = false;
  let releaseRead: (() => void) | undefined;
  let holdRevision = false;
  let releaseRevision: (() => void) | undefined;
  let heldRevisionStatus = 200;
  let holdPending = false;
  let releasePending: (() => void) | undefined;
  let entries: JudgmentHistoryEntry[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
    if (path.endsWith("/escalated")) return json([]);
    if (path.endsWith("/escalated/42/resolve")) {
      const out = json({ error: { code: "unavailable", message: "Controlled pending unknown" } }, 500);
      if (holdPending) return await new Promise<Response>(resolve => { releasePending = () => resolve(out); });
      return out;
    }
    if (path.endsWith("/report")) return json(report());
    if (path.endsWith("/judgment/history")) return failHistory ? json({ error: { code: "unavailable", message: "Controlled history outage" } }, 503)
      : json({ check_run_id: 5, criterion_id: 101, check_result_id: 41, assessment_revision: epoch,
        judgment_revision: current.judgment_revision, current, entries, history_incomplete: historyIncomplete, next_cursor: null });
    if (path.endsWith("/judgment")) {
      const out = failRead ? json({ error: { code: "unavailable", message: "Controlled current outage" } }, readStatus)
        : json(wrongReadResult ? { ...context(), check_result_id: 999 } : context());
      if (holdRead) { holdRead = false; return await new Promise<Response>(resolve => { releaseRead = () => resolve(out); }); }
      return out;
    }
    if (path.endsWith("/judgment/revisions")) {
      const body = JSON.parse(init!.body as string) as JudgmentRevisionInput;
      posts.push(body);
      if (response === "missing") return json({ error: { code: "not_found", message: "Criterion unavailable" } }, 404);
      if (response === "conflict") { epoch += 1; return json({ error: { code: "conflict", message: "Controlled sibling change" } }, 409); }
      if (response === "unknown") throw new TypeError("Controlled network loss");
      const before = structuredClone(current);
      current = { ...current, judgment_revision: current.judgment_revision + 1,
        resolution_type: body.resolution, reason: body.reason, recorded_at: "2026-10-23T01:00:00+00:00", provenance: "recorded", actor_id: 1,
        level: body.resolution === "mark_level" ? { ordinal: body.level!, name: context().choices.levels.find(value => value.level === body.level)!.name, points: 0, max_points: 3 } : null,
        outcome: body.resolution === "needs_document" ? "not_applicable" : body.resolution === "mark_fail" ? "failed" : "passed",
        score: body.resolution === "needs_document" ? null : body.resolution === "mark_fail" || body.resolution === "mark_level" ? 0 : 100 };
      epoch += 1;
      const entry: JudgmentHistoryEntry = { schema_version: 1, entry_id: epoch, check_run_id: 5, criterion_id: 101, check_result_id: 41,
        action: "judgment_revised", before, after: current, assessment_revision_before: epoch - 1, assessment_revision_after: epoch,
        performed_by: 1, performed_at: current.recorded_at! };
      entries = [entry, ...entries];
      const out = { context: context(), entry: response === "malformed" ? { ...entry, criterion_id: 999 } : entry, report: report() };
      if (response === "wrong_result") {
        out.context.check_result_id = 999; out.entry.check_result_id = 999; out.report.results[0].check_result_id = 999;
      }
      if (holdRevision) return await new Promise<Response>(resolve => {
        releaseRevision = () => resolve(heldRevisionStatus === 200 ? json(out)
          : json({ error: { code: "unauthorized", message: "Old request refused" } }, heldRevisionStatus));
      });
      return json(out);
    }
    return json({ error: { code: "missing", message: "No fixture route" } }, 404);
  }));
  let client!: QueryClient;
  if (options.actualAuthRoute) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(ME_QUERY_KEY, { id: 1 });
    const router = createMemoryRouter([
      { path: "/report/:checkRunId", element: <RequireAuth><CriterionReviewLayout /></RequireAuth>,
        children: [{ index: true, element: <Fixture captureClient={() => {}} /> }] },
      { path: "/signin", element: <><p>Sign in</p><button>Another task</button></> },
    ], { initialEntries: ["/report/5"] });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  } else renderWithReviewProviders(<Fixture captureClient={value => { client = value; }} />, { reviewRunId: 5, route: "/report/5" });
  return { client: () => client, holdRevision: (status = 200) => { holdRevision = true; heldRevisionStatus = status; },
    releaseRevision: () => releaseRevision?.(), posts, response: (value: typeof response) => { response = value; }, failRead: (value: boolean, status = 503) => { failRead = value; readStatus = status; },
    wrongReadResult: () => { wrongReadResult = true; },
    holdRead: () => { holdRead = true; }, releaseRead: () => releaseRead?.(),
    holdPending: () => { holdPending = true; }, releasePending: () => releasePending?.(),
    history: (incomplete: boolean, fail = false) => { historyIncomplete = incomplete; failHistory = fail; } };
}
async function openRevision() {
  fireEvent.click(await screen.findByRole("button", { name: /^Revise recorded judgment:/ }));
  return await screen.findByRole("textbox", { name: "New reason for revision (required)" });
}
function review(reason = NEW_REASON) {
  fireEvent.change(screen.getByRole("textbox", { name: "New reason for revision (required)" }), { target: { value: reason } });
  fireEvent.click(screen.getByRole("button", { name: "Review revision" }));
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Explicit saved-judgment revision", () => {
  it("shows an omitted ordinary initial comment in the preview while requiring the new revision reason", async () => {
    const control = setup({ blankComment: true });
    const reason = await openRevision();
    expect(reason).toBeRequired();
    review();
    const dialog = screen.getByRole("dialog", { name: "Save revised judgment?" });
    expect(within(dialog).getByText("No comment recorded.")).toBeVisible();
    expect(control.posts).toEqual([]);
  });
  it.each([1, 2, null].flatMap(nextId => [200, 401].map(status => ({ nextId, status }))))(
    "retires a delayed revision POST after auth replacement $nextId with response $status", async ({ nextId, status }) => {
      const control = setup({ actualAuthRoute: true }); await openRevision(); review(); control.holdRevision(status);
      fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
      await waitFor(() => expect(control.posts).toHaveLength(1));
      const client = control.client();
      await act(async () => {
        if (nextId === null) client.setQueryData(ME_QUERY_KEY, null);
        else { client.clear(); client.setQueryData(ME_QUERY_KEY, { id: nextId }); }
      });
      if (nextId === null) await screen.findByText("Sign in");
      else await screen.findByRole("button", { name: /^Revise recorded judgment:/ });
      await waitFor(() => expect(client.isFetching()).toBe(0));
      const replacementAuth = client.getQueryData(ME_QUERY_KEY);
      const replacementReport = client.getQueryData(["report", 5]);
      const other = screen.getByRole("button", { name: "Another task" }); other.focus();
      await act(async () => control.releaseRevision());
      expect(client.getQueryData(ME_QUERY_KEY)).toBe(replacementAuth);
      expect(client.getQueryData(["report", 5])).toBe(replacementReport);
      expect(screen.queryByRole("heading", { name: "Judgment revised" })).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue(NEW_REASON)).not.toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(other).toHaveFocus(); expect(control.posts).toHaveLength(1);
    });
  it("warns on unload only after an actual revision change", async () => {
    setup(); await openRevision();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: NEW_REASON } });
    const changed = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(changed);
    expect(changed.defaultPrevented).toBe(true);
  });
  it("retires the criterion's private context and draft after a terminal unavailable read", async () => {
    const control = setup(); await openRevision(); review(); control.response("conflict");
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    control.failRead(true, 404);
    fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    expect(await screen.findByRole("heading", { name: "Recorded criterion unavailable" })).toBeVisible();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(OLD_REASON)).not.toBeInTheDocument();
    expect(screen.queryByText(NEW_REASON)).not.toBeInTheDocument();
    expect(screen.queryByText("Original system explanation.")).not.toBeInTheDocument();
  });
  it("loads a blank new reason, keeps source evidence and requires separate review then save", async () => {
    const control = setup();
    expect(await openRevision()).toHaveValue("");
    expect(screen.getAllByText(OLD_REASON)).toHaveLength(1);
    fireEvent.click(screen.getByLabelText("Does not meet"));
    review();
    const dialog = await screen.findByRole("dialog", { name: "Save revised judgment?" });
    expect(control.posts).toHaveLength(0);
    expect(within(dialog).getByText(OLD_REASON)).toBeVisible();
    expect(within(dialog).getByText(NEW_REASON)).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Back to editing" }));
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON);
    fireEvent.click(screen.getByRole("button", { name: "Review revision" }));
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("heading", { name: "Judgment revised" })).toBeVisible();
    expect(control.posts).toEqual([{ expected_revision: 1, expected_judgment_revision: 1, resolution: "mark_fail", reason: NEW_REASON }]);
    fireEvent.click(screen.getByRole("button", { name: "View judgment history" }));
    expect(await screen.findByRole("heading", { name: /Current at time/ })).toBeVisible();
    expect(screen.getAllByText(OLD_REASON)).toHaveLength(1);
  });
  it("rejects identical grade/reason without creating a confirmation or request", async () => {
    const control = setup(); await openRevision(); review(OLD_REASON);
    expect(screen.getByRole("alert")).toHaveTextContent("Change the judgment or enter a new reason before saving.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(control.posts).toHaveLength(0);
  });
  it("keeps missing legacy grade, time and actor unavailable and supports ordinal zero", async () => {
    const control = setup({ legacy: true }); await openRevision();
    expect(screen.getByText("Original save time unavailable.", { exact: false })).toBeVisible();
    expect(screen.getByText("Instructor attribution unavailable.", { exact: false })).toBeVisible();
    expect(screen.getByRole("radio", { name: /No evidence/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /No evidence/ })); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(control.posts).toHaveLength(1));
    expect(control.posts[0].level).toBe(0); expect(control.posts[0].expected_judgment_revision).toBe(0);
  });
  it.each(["conflict", "unknown", "malformed", "wrong_result"] as const)("retains the entered proposal after %s without replaying POST", async mode => {
    const control = setup(); control.response(mode); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON);
    expect(screen.getByRole("button", { name: "Review revision" })).toBeDisabled();
    expect(control.posts).toHaveLength(1);
    if (mode !== "conflict") expect(screen.getByRole("heading", { name: "Revision save is unconfirmed" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    await waitFor(() => expect(screen.queryByText("Loading the current judgment.")).not.toBeInTheDocument());
    expect(control.posts).toHaveLength(1);
    if (mode === "unknown") expect(screen.queryByRole("button", { name: "Use this draft with the current judgment" })).not.toBeInTheDocument();
    else {
      const rebind = screen.getByRole("button", { name: "Use this draft with the current judgment" });
      rebind.focus(); fireEvent.click(rebind);
      expect(screen.getByRole("textbox")).toHaveFocus();
      expect(screen.getByRole("button", { name: "Review revision" })).toBeEnabled();
    }
  });
  it("rejects current context whose result identity differs from its coherent report row", async () => {
    const control = setup(); control.wrongReadResult();
    fireEvent.click(await screen.findByRole("button", { name: /^Revise recorded judgment:/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The current judgment could not be loaded");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument(); expect(control.posts).toHaveLength(0);
  });
  it("clears this criterion's private draft, history and prior receipt after a save returns unavailable", async () => {
    const control = setup(); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "View judgment history" }));
    expect(await screen.findByRole("heading", { name: /Current at time/ })).toBeVisible();
    await openRevision();
    const later = "A replacement private reason that must retire on a terminal refusal.";
    review(later); control.response("missing");
    const save = screen.getByRole("button", { name: "Save revised judgment" }); save.focus(); fireEvent.click(save);
    expect(await screen.findByRole("heading", { name: "Recorded criterion unavailable" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Recorded criterion unavailable" })).toHaveFocus();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Judgment revised" })).not.toBeInTheDocument();
    expect(screen.queryByText(OLD_REASON)).not.toBeInTheDocument(); expect(screen.queryByText(NEW_REASON)).not.toBeInTheDocument();
    expect(screen.queryByText(later)).not.toBeInTheDocument(); expect(control.posts).toHaveLength(2);
  });
  it.each([false, true])("can stop local editing with an unknown same-epoch save (current GET fails=%s)", async fail => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    control.failRead(fail);
    fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    await waitFor(() => expect(screen.queryByText("Loading the current judgment.")).not.toBeInTheDocument());
    const stop = screen.getByRole("button", { name: "Stop editing this revision" }); stop.focus(); fireEvent.click(stop);
    const dialog = screen.getByRole("dialog", { name: "Stop editing this revision?" });
    expect(dialog).toHaveTextContent("does not cancel or reverse it");
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep draft" }));
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON);
    fireEvent.click(stop);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Stop editing" }));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current instructor judgment", level: 4 })).toHaveFocus();
    expect(screen.getByText(/^Editing stopped\./)).toBeVisible(); expect(control.posts).toHaveLength(1);
    expect(screen.queryByText(/Your draft is kept/)).not.toBeInTheDocument();
    control.failRead(false);
    await openRevision();
    expect(screen.getByRole("textbox")).toHaveValue("");
    review("A distinct newly entered reason after a fresh current read.");
    expect(screen.getByRole("dialog")).toHaveTextContent("This is a new revision of the current judgment shown above");
    expect(control.posts).toHaveLength(1);
  });
  it("can explicitly leave an unknown revision without resending or claiming cancellation", async () => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("link", { name: "Leave review" }));
    const exit = screen.getByRole("dialog", { name: "Leave with an unconfirmed revision save?" });
    expect(exit).toHaveTextContent("does not cancel or reverse the earlier request");
    fireEvent.click(within(exit).getByRole("button", { name: "Stop editing and leave" }));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/elsewhere"));
    expect(control.posts).toHaveLength(1);
  });
  it("refuses an exit snapshot after the revision draft was replaced", async () => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("link", { name: "Leave review" }));
    const exit = screen.getByRole("dialog");
    // Controlled background replacement bypasses inert DOM to exercise the guard.
    fireEvent.click(screen.getByText("Change captured revision draft"));
    fireEvent.click(within(exit).getByRole("button", { name: "Stop editing and leave" }));
    expect(screen.getByTestId("path")).toHaveTextContent("/report/5");
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON);
  });
  it("keeps the separate pending-resolution unknown guard in a mixed review", async () => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Start controlled pending save" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop editing this revision" })).toBeEnabled());
    fireEvent.click(screen.getByRole("link", { name: "Leave review" }));
    expect(screen.getByRole("dialog", { name: "Stay in this review" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Stop editing and leave" })).not.toBeInTheDocument();
    expect(screen.getByTestId("path")).toHaveTextContent("/report/5");
  });
  it("disables local stop while another criterion write is actively awaiting completion", async () => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    control.holdPending(); fireEvent.click(screen.getByRole("button", { name: "Start controlled pending save" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop editing this revision" })).toBeDisabled());
    await act(async () => control.releasePending());
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop editing this revision" })).toBeEnabled());
  });
  it("does not let a delayed read revive an abandoned draft or clear its replacement", async () => {
    const control = setup(); control.response("unknown"); await openRevision(); review();
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    control.holdRead(); fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop editing this revision" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Stop editing" }));
    await openRevision();
    const replacement = "A new draft whose reason belongs only to this new editing action.";
    fireEvent.change(screen.getByRole("textbox"), { target: { value: replacement } });
    const other = screen.getByRole("button", { name: "Another task" }); other.focus();
    await act(async () => control.releaseRead());
    expect(screen.getByRole("textbox")).toHaveValue(replacement);
    expect(other).toHaveFocus(); expect(control.posts).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Judgment revised" })).not.toBeInTheDocument();
  });
  it("distinguishes an incomplete empty history from loading errors and genuine empty history", async () => {
    const control = setup(); control.history(true);
    fireEvent.click(await screen.findByRole("button", { name: /^Judgment history:/ }));
    expect(await screen.findByText("No complete history entries are available.")).toBeVisible();
    expect(screen.queryByText("No recorded revisions yet.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Judgment history:/ }));
    control.history(false, true); fireEvent.click(screen.getByRole("button", { name: /^Judgment history:/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Judgment history could not be loaded");
    expect(screen.queryByText("No recorded revisions yet.")).not.toBeInTheDocument();
  });
  it("preserves the reason after a current-read outage and can cancel only this revision", async () => {
    const control = setup(); await openRevision();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: NEW_REASON } });
    control.failRead(true); control.response("conflict"); fireEvent.click(screen.getByRole("button", { name: "Review revision" }));
    fireEvent.click(screen.getByRole("button", { name: "Save revised judgment" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Review current judgment" }));
    expect(await screen.findByText("The current judgment could not be loaded.")).toBeVisible();
    expect(screen.getByText("Your draft is kept.")).toBeVisible();
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON);
    fireEvent.click(screen.getByRole("button", { name: "Cancel revision" }));
    const discard = screen.getByRole("dialog", { name: "Discard this unsaved revision?" });
    fireEvent.click(within(discard).getByRole("button", { name: "Discard revision" }));
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
    expect(control.posts).toHaveLength(1);
    await act(async () => {});
  });
});


describe("revision action provenance", () => {
  it("keeps visible short context on revision entry and inside confirmation actions", async () => {
    const control = setup();
    const revise = await screen.findByRole("button", { name: /^Revise recorded judgment:/ });
    expect(revise).toHaveAccessibleDescription(/AI response mode is unverified/);
    for (const id of revise.getAttribute("aria-describedby")!.split(/\s+/)) expect(document.getElementById(id)).toBeVisible();
    fireEvent.click(revise);
    const reason = await screen.findByRole("textbox");
    fireEvent.change(reason, { target: { value: NEW_REASON.repeat(12) } });
    const review = screen.getByRole("button", { name: "Review revision" });
    expect(review).toHaveAccessibleDescription(/AI response mode is unverified/);
    fireEvent.click(review);
    const dialog = await screen.findByRole("dialog", { name: "Save revised judgment?" });
    const save = within(dialog).getByRole("button", { name: "Save revised judgment" });
    expect(save).toHaveAccessibleDescription(/AI response mode is unverified/);
    const footer = save.closest("footer")!;
    expect(within(footer).getByText("AI response mode is unverified.")).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Back to editing" }));
    expect(screen.getByRole("textbox")).toHaveValue(NEW_REASON.repeat(12));
    expect(control.posts).toHaveLength(0);
  });
});
