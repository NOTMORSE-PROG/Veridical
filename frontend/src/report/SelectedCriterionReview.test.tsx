import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscalatedItemOut, ResultRowCommon } from "../api/types";
import { renderWithReviewProviders } from "../test/renderWithProviders";
import { SignalEscalatedPanel } from "./SignalReviewSections";

const items: EscalatedItemOut[] = [13, 14].map((id, index) => ({
  check_result_id: index + 4, criterion_id: id, criterion_text: `Custom criterion ${id}`,
  weight: 1, agreement: null, votes: [], ai_majority_verdict: null, reason: null, unverified_evidence: null,
  review_reason: "low_confidence", evidence: [{ quote: `Passage for ${id}`, anchor: `paragraph ${index}` }],
}));

function setup(search = "view=decision&pane=review") {
  let unresolved = [...items];
  let results: ResultRowCommon[] = [];
  let revision = 1;
  const report = () => ({ check_run_id: 5, assessment_revision: revision, decision: null,
    results, pending_review_count: unresolved.length });
  const requests: unknown[] = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (init?.method === "POST") {
      const body = JSON.parse(init.body as string);
      requests.push(body);
      const id = Number(path.match(/escalated\/(\d+)/)?.[1]);
      const item = items.find(row => row.check_result_id === id)!;
      const row: ResultRowCommon = { criterion_id: item.criterion_id, text: item.criterion_text,
        type: "semantic", kind: "semantic", weight: 1, weight_importance: "med", outcome: "passed",
        score: 100, basis: "llm", anchor: null, evidence: [], reasoning: null, reason: null,
        resolution: { type: "mark_pass", reason: body.reason, ai_majority_verdict: null } };
      results = [...results, row];
      unresolved = unresolved.filter(row => row.check_result_id !== id);
      revision += 1;
      return new Response(JSON.stringify({ check_result_id: id, outcome: "passed", score: 100, report: report() }));
    }
    if (path.endsWith("/escalated")) return new Response(JSON.stringify(unresolved));
    if (path.endsWith("/report")) return new Response(JSON.stringify(report()));
    return new Response(JSON.stringify({ instructor: { id: 1 } }));
  });
  vi.stubGlobal("fetch", fetch);
  renderWithReviewProviders(<SignalEscalatedPanel checkRunId={5} />, { route: `/report/5/document?${search}` });
  return { requests, fetch };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("one selected desktop criterion", () => {
  it("starts with compact rows and no mounted grading forms", async () => {
    setup();
    expect(await screen.findAllByRole("link", { name: /Review criterion:/ })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Meets criterion" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review criterion: Custom criterion 13" })).toHaveAttribute("href", "/report/5/document?view=decision&pane=review&criterion=13");
  });

  it("mounts one form and restores each draft after explicit navigation", async () => {
    const { requests } = setup();
    fireEvent.click(await screen.findByRole("link", { name: "Review criterion: Custom criterion 13" }));
    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));
    const first = screen.getByRole("textbox", { name: "Comment (optional)" });
    fireEvent.change(first, { target: { value: "Keep first private entry" } });
    fireEvent.click(screen.getByRole("link", { name: "Next criterion" }));
    expect(first.isConnected).toBe(false);
    expect(screen.getAllByRole("button", { name: "Meets criterion" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Does not meet" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep second private entry" } });
    fireEvent.click(screen.getByRole("link", { name: "Previous criterion" }));
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.getByRole("textbox")).toHaveValue("Keep first private entry");
    fireEvent.click(screen.getByRole("link", { name: "Next criterion" }));
    expect(screen.getByRole("textbox")).toHaveValue("Keep second private entry");
    expect(requests).toEqual([]);
  });

  it("saves an ordinary grade without a comment and keeps the selected identity", async () => {
    const { requests } = setup("view=decision&pane=review&criterion=13");
    fireEvent.click(await screen.findByRole("button", { name: "Meets criterion" }));
    expect(screen.getByRole("textbox", { name: "Comment (optional)" })).not.toBeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Save grade" }));
    expect(await screen.findByRole("heading", { name: "Judgment saved" })).toBeVisible();
    expect(requests).toEqual([{ resolution: "mark_pass", reason: "", expected_revision: 1 }]);
    expect(screen.queryByRole("button", { name: "Meets criterion" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Custom criterion 14" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Back to criteria" }));
    expect(await screen.findByRole("link", { name: "Review criterion: Custom criterion 14" })).toBeVisible();
  });

  it("keeps document exclusion reason-required and unsaved when blank", async () => {
    const { requests } = setup("view=decision&pane=review&criterion=13");
    fireEvent.click(await screen.findByRole("button", { name: "Exclude: needs another document" }));
    expect(screen.getByRole("textbox")).toBeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Confirm exclusion" }));
    expect(await screen.findByText("Enter a reason before confirming.")).toBeVisible();
    expect(requests).toEqual([]);
  });

  it.each(["14.0", "999", "bad", ""]) ("never substitutes another form for unavailable selection %s", async raw => {
    setup(`view=decision&pane=review&criterion=${raw}`);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Criterion not available" })).toBeVisible());
    expect(screen.queryByRole("button", { name: "Meets criterion" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to criteria" })).toBeVisible();
  });
});
