import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { EscalatedPanel } from "./EscalatedPanel";

const ITEM_WITH_MAJORITY = {
  check_result_id: 3,
  criterion_id: 3,
  criterion_text: "Methodology justifies the chosen design",
  weight: 40,
  agreement: 0.667,
  votes: ["pass", "fail", "pass"],
  ai_majority_verdict: "pass",
  reason: null,
  review_reason: "low_confidence",
};

// The AI never ran on this one (daily capacity spent) — a different fact
// from "the AI was unsure", and the panel must not blur them (V-050).
const ITEM_NOT_GRADED = {
  ...ITEM_WITH_MAJORITY,
  check_result_id: 5,
  criterion_id: 5,
  criterion_text: "Results are discussed against the literature",
  agreement: null,
  votes: [],
  ai_majority_verdict: null,
  reason: "Not graded by AI — today's free AI capacity was reached.",
  review_reason: "not_graded",
};

const ITEM_WITHOUT_MAJORITY = {
  ...ITEM_WITH_MAJORITY,
  check_result_id: 4,
  criterion_id: 4,
  criterion_text: "Objectives are measurable and time-bound",
  agreement: 0.333,
  votes: ["pass", "fail", "partial"],
  ai_majority_verdict: null,
};

describe("EscalatedPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders nothing when there is nothing escalated", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/1/escalated": [] }));
    const { container } = renderWithProviders(<EscalatedPanel checkRunId={1} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("labels a never-graded item as such instead of implying the AI voted", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/1/escalated": [ITEM_NOT_GRADED] }));
    renderWithProviders(<EscalatedPanel checkRunId={1} />);
    expect(await screen.findByText(/Not graded by AI · Semantic/)).toBeInTheDocument();
    // "No votes recorded" would suggest a vote was attempted and came back
    // empty; nothing was ever sent.
    expect(screen.queryByText(/No votes recorded/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Agreement/)).not.toBeInTheDocument();
  });

  it("shows the agreement fraction and criterion text", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/1/escalated": [ITEM_WITH_MAJORITY] }),
    );
    renderWithProviders(<EscalatedPanel checkRunId={1} />);
    expect(await screen.findByText("Needs your review (1)")).toBeInTheDocument();
    expect(screen.getByText(/Agreement 2\/3/)).toBeInTheDocument();
  });

  it("disables Accept AI when there is no AI majority to accept", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/1/escalated": [ITEM_WITHOUT_MAJORITY] }),
    );
    renderWithProviders(<EscalatedPanel checkRunId={1} />);
    await screen.findByText("Needs your review (1)");
    expect(screen.getByRole("button", { name: "Accept AI" })).toBeDisabled();
  });

  it("requires a reason before the Confirm button is enabled", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/1/escalated": [ITEM_WITH_MAJORITY] }),
    );
    renderWithProviders(<EscalatedPanel checkRunId={1} />);
    await screen.findByText("Needs your review (1)");
    fireEvent.click(screen.getByRole("button", { name: "Pass" }));
    const confirm = await screen.findByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("Why are you resolving this way?"), {
      target: { value: "Checked the excerpt myself." },
    });
    expect(confirm).toBeEnabled();
  });

  it("submits the resolution with reason and resolution type", async () => {
    const fetchMock = stubFetchByPath({
      "/check-runs/1/escalated": [ITEM_WITH_MAJORITY],
      "/check-runs/1/escalated/3/resolve": {
        check_result_id: 3,
        outcome: "passed",
        score: 100,
        report: { check_run_id: 1, results: [] },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<EscalatedPanel checkRunId={1} />);
    await screen.findByText("Needs your review (1)");
    fireEvent.click(screen.getByRole("button", { name: "Accept AI" }));
    fireEvent.change(await screen.findByPlaceholderText("Why are you resolving this way?"), {
      target: { value: "Agreed with the AI." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    const resolveCall = await new Promise<unknown[]>((resolve) => {
      const check = () => {
        const call = fetchMock.mock.calls.find((c) =>
          String(c[0]).includes("/escalated/3/resolve"),
        );
        if (call) resolve(call);
        else setTimeout(check, 10);
      };
      check();
    });
    const init = resolveCall[1] as RequestInit;
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ resolution: "accept_majority", reason: "Agreed with the AI." });
  });
});
