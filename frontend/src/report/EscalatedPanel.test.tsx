import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscalatedItemOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { EscalatedPanel } from "./EscalatedPanel";

const SPLIT_VOTE: EscalatedItemOut = {
  check_result_id: 1,
  criterion_id: 10,
  criterion_text: "Reference list has at least five major sources",
  weight: 20,
  agreement: 0.5,
  votes: ["pass", "fail"],
  ai_majority_verdict: null,
  reason: null,
  review_reason: "low_confidence",
};

const BOTH_NO_VERDICT: EscalatedItemOut = {
  check_result_id: 2,
  criterion_id: 11,
  criterion_text: "Findings are discussed with reference to prior literature",
  weight: 20,
  agreement: 0,
  votes: [null, null],
  ai_majority_verdict: null,
  reason: "Could not verify the quoted evidence after a retry.",
  review_reason: "low_confidence",
};

const NOT_GRADED: EscalatedItemOut = {
  check_result_id: 3,
  criterion_id: 12,
  criterion_text: "Chapter 1 states the research problem",
  weight: 20,
  agreement: null,
  votes: [],
  ai_majority_verdict: null,
  reason: null,
  review_reason: "not_graded",
};

const REAL_MAJORITY: EscalatedItemOut = {
  check_result_id: 4,
  criterion_id: 13,
  criterion_text: "Abstract states the study's purpose",
  weight: 20,
  agreement: 0.5,
  votes: ["pass", "pass"],
  ai_majority_verdict: "pass",
  reason: null,
  review_reason: "low_confidence",
};

describe("EscalatedPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders nothing when there are no escalated items", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [] }));
    const { container } = renderWithProviders(<EscalatedPanel checkRunId={5} />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("distinguishes a genuine split vote from neither pass producing a verdict (never reads alike)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/check-runs/5/escalated": [SPLIT_VOTE, BOTH_NO_VERDICT] }),
    );
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    expect(await screen.findByText("Split vote: pass, fail.")).toBeInTheDocument();
    expect(screen.getByText("No valid verdict from 2 grading passes.")).toBeInTheDocument();
  });

  it("labels a not_graded item distinctly from a low-confidence one (never claims a vote that never happened)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [NOT_GRADED] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    expect(await screen.findByText("Not graded by AI.")).toBeInTheDocument();
  });

  it("does not render an Accept AI button when there is no AI majority verdict to accept (never disabled+tooltip)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [BOTH_NO_VERDICT] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    await screen.findByText("No valid verdict from 2 grading passes.");
    expect(screen.queryByRole("button", { name: /Accept AI/ })).not.toBeInTheDocument();
    expect(
      screen.getByText("The AI reached no verdict to accept for this criterion."),
    ).toBeInTheDocument();
  });

  it("shows the real verdict word on the Accept AI button when a majority exists", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [REAL_MAJORITY] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    expect(await screen.findByRole("button", { name: "Accept AI: pass" })).toBeInTheDocument();
  });

  it("requires a reason before confirming, validated on click (Confirm is never pre-disabled)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [REAL_MAJORITY] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    fireEvent.click(await screen.findByRole("button", { name: "Pass" }));
    const confirmButton = screen.getByRole("button", { name: "Confirm" });
    expect(confirmButton).not.toBeDisabled();
    fireEvent.click(confirmButton);

    expect(await screen.findByText("Enter a reason before confirming.")).toBeInTheDocument();
    const reasonInput = screen.getByPlaceholderText("Why are you resolving this way?");
    expect(reasonInput).toHaveAttribute("aria-invalid", "true");
  });

  it("submits the resolution with the reason and updates the announcement on success", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/resolve") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            check_result_id: 4,
            outcome: "passed",
            score: 100,
            report: {
              check_run_id: 5,
              manuscript_group_label: "Ungrouped",
              rubric_title: "Format",
              status: "ready",
              composite_score: 95,
              thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
              reason: null,
              flag_deduction: 0,
              unresolved_high_flag_count: 0,
              results: [],
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes("/escalated")) return new Response(JSON.stringify([REAL_MAJORITY]), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    fireEvent.click(await screen.findByRole("button", { name: "Accept AI: pass" }));
    fireEvent.change(screen.getByPlaceholderText("Why are you resolving this way?"), {
      target: { value: "AI's grading looks correct on review." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/resolve"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const call = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    const init = call![1] as RequestInit;
    const body = JSON.parse(init.body as string) as { resolution: string; reason: string };
    expect(body).toEqual({ resolution: "accept_majority", reason: "AI's grading looks correct on review." });
  });
});
