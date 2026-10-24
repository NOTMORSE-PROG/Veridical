import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EscalatedItemOut } from "../api/types";
import { renderWithReviewProviders as renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignalEscalatedPanel } from "./SignalReviewSections";

function record(patch: Partial<EscalatedItemOut> = {}): EscalatedItemOut {
  return {
    check_result_id: 8, criterion_id: 3, criterion_text: "Custom requirement needing review",
    weight: 1, agreement: null, votes: ["Proficient", null], ai_majority_verdict: null,
    reason: null, reasoning: "The actual recorded explanation.", evidence: [],
    review_reason: "low_confidence", unverified_evidence: null, ...patch,
  };
}

async function show(item: EscalatedItemOut) {
  vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [item] }));
  renderWithProviders(<SignalEscalatedPanel checkRunId={5} />);
  await screen.findByRole("heading", { name: item.criterion_text });
}

describe("instructor assessment interpretation", () => {
  it("does not offer acceptance when an explicit support code lacks the optional withholding boolean", async () => {
    await show(record({ review_reason: "support_incomplete", ai_majority_verdict: "pass" }));
    expect(screen.getByText("The separate evidence review did not finish.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Accept AI suggestion/ })).not.toBeInTheDocument();
  });
  it("does not turn one suggestion and one missing answer into disagreement", async () => {
    await show(record());
    expect(screen.getByText("No grade accepted")).toBeVisible();
    expect(screen.queryByText(/Split vote:/)).not.toBeInTheDocument();
    expect(screen.getByText("The actual recorded explanation.")).toBeVisible();
  });

  it("states missing explanation and located evidence even without a tentative majority", async () => {
    await show(record({ votes: [null, null], reasoning: " \n\t " }));
    expect(screen.getByText("No explanation is available in this assessment record.")).toBeVisible();
    expect(screen.getByText("No located excerpt is available for this review item.")).toBeVisible();
    expect(screen.queryByText(/No valid verdict|No AI assessment was recorded/)).not.toBeInTheDocument();
  });

  it("keeps independent source and grade limitations visible when support is also incomplete", async () => {
    await show(record({
      support_withheld: true, support_status: "incomplete", review_reason: "support_incomplete",
      injection_suspected: true, injection_matched_snippet: null, verdict_unrecognized: true,
    }));
    expect(screen.getByText("The separate evidence review did not finish.")).toBeVisible();
    expect(screen.getByText("Text that may be directed at automated grading needs review.")).toBeVisible();
    expect(screen.getByText("The recorded suggestion is not a recognized grade for this criterion.")).toBeVisible();
    const choice = screen.getByRole("button", { name: "Meets criterion" });
    expect(choice).toHaveAccessibleDescription(/separate evidence review did not finish/);
    expect(choice).toHaveAccessibleDescription(/automated grading needs review/);
    expect(choice).toHaveAccessibleDescription(/not a recognized grade/);
  });

  it("retains unmapped essential reasons and their action association", async () => {
    const reason = "The recorded source region could not be used; inspect the full manuscript.";
    await show(record({ reason }));
    expect(screen.getByText(reason)).toBeVisible();
    expect(screen.getByRole("button", { name: "Meets criterion" })).toHaveAccessibleDescription(new RegExp(reason));
  });

  it("makes raw result entries optional without attaching them to grading controls", async () => {
    await show(record());
    const toggle = screen.getByRole("button", { name: /Show assessment record/ });
    const choice = screen.getByRole("button", { name: "Meets criterion" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const entries = screen.getByRole("list", { name: "Recorded result entries" });
    expect(within(entries).getByText("Proficient")).toBeVisible();
    expect(within(entries).getByText("No grade label recorded.")).toBeVisible();
    expect(choice).not.toHaveAccessibleDescription(/Entry 1|No grade label recorded/);
    fireEvent.click(screen.getByRole("button", { name: /Hide assessment record/ }));
    expect(entries).not.toBeVisible();
    expect(choice).not.toHaveAccessibleDescription(/Entry 1|No grade label recorded/);
  });
});


describe("short consequential action descriptions", () => {
  it("keeps rejected quotes and prompt-directed text visible without repeating their content on every choice", async () => {
    const rejected = "A rejected quotation that is not a verified manuscript location.";
    const directed = "A lengthy instruction aimed at a grading system.";
    await show(record({ injection_suspected: true, injection_matched_snippet: directed,
      unverified_evidence: [rejected], reason: "The source still needs direct inspection." }));
    const action = screen.getByRole("button", { name: "Meets criterion" });
    expect(action).toHaveAccessibleDescription(/Could not verify against the source/);
    expect(action).toHaveAccessibleDescription(/Text addressed at an automated grader/);
    expect(action).not.toHaveAccessibleDescription(new RegExp(rejected));
    expect(action).not.toHaveAccessibleDescription(new RegExp(directed));
    expect(screen.getByText(new RegExp(rejected))).toBeVisible();
    expect(screen.getByText(new RegExp(directed))).toBeVisible();
    expect(screen.queryByRole("link", { name: /Open in manuscript/ })).not.toBeInTheDocument();
  });
  it("puts recorded source passages before the recorded AI explanation", async () => {
    await show(record({ reasoning: "An explanation must remain inspectable.", evidence: [{quote: "Actual recorded source.", anchor: "paragraph 2"}] }));
    const passage = screen.getByText(/Actual recorded source/);
    const explanation = screen.getByText("An explanation must remain inspectable.");
    expect(passage.compareDocumentPosition(explanation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(explanation).toBeVisible();
  });
});
