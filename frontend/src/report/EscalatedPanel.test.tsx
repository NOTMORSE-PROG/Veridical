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
  unverified_evidence: null,
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
  unverified_evidence: ["a quote that could not be verified against the source"],
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
  unverified_evidence: null,
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
  unverified_evidence: null,
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

  it("V-068 Q1/Q2: renders unverified evidence under its own label, never merged into a verified block", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [BOTH_NO_VERDICT] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    expect(await screen.findByText("Could not verify against the source")).toBeInTheDocument();
    expect(
      screen.getByText('"a quote that could not be verified against the source"'),
    ).toBeInTheDocument();
  });

  it("V-068 AC2: always offers a third option that is not a guess", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [REAL_MAJORITY] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    expect(
      await screen.findByRole("button", { name: "Needs the document" }),
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

    const error = await screen.findByText("Enter a reason before confirming.");
    expect(error).toBeInTheDocument();
    // `ux-critic` finding (WCAG 4.1.3): must be announced without the
    // user having to tab back to the field.
    expect(error).toHaveAttribute("role", "alert");
    const reasonInput = screen.getByPlaceholderText("Why are you resolving this way?");
    expect(reasonInput).toHaveAttribute("aria-invalid", "true");
  });

  it("BUG-096: rejects a one-character reason instead of publishing it verbatim", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/escalated": [REAL_MAJORITY] }));
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    fireEvent.click(await screen.findByRole("button", { name: "Pass" }));
    const reasonInput = screen.getByPlaceholderText("Why are you resolving this way?");
    fireEvent.change(reasonInput, { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(
      await screen.findByText(/Reason must be at least 10 characters/),
    ).toBeInTheDocument();
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

  it("V-068 AC2: submits needs_document as its own resolution, distinct from pass/fail", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/resolve") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            check_result_id: 4,
            outcome: "not_applicable",
            score: null,
            report: {
              check_run_id: 5,
              manuscript_group_label: "Ungrouped",
              rubric_title: "Format",
              status: "needs_review",
              composite_score: null,
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

    fireEvent.click(await screen.findByRole("button", { name: "Needs the document" }));
    fireEvent.change(screen.getByPlaceholderText("Why are you resolving this way?"), {
      target: { value: "Cannot judge this without opening the manuscript myself." },
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
    expect(body.resolution).toBe("needs_document");
    expect(
      await screen.findByText("Resolved as excluded (needs the document). Composite score is now unavailable%."),
    ).toBeInTheDocument();
  });

  it("V-071 (BUG-054, live-reproduced 3/3 times): focus moves to the panel heading after a resolution, not <body>", async () => {
    let resolved = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/resolve") && init?.method === "POST") {
        resolved = true;
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
      if (url.includes("/escalated")) {
        // Two items pending, then one once the first is resolved -- the
        // panel stays mounted throughout, so this exercises the "focus
        // stays on the still-rendered heading" case, not the separate
        // "last item resolved, panel unmounts" case this fix doesn't cover.
        return new Response(JSON.stringify(resolved ? [SPLIT_VOTE] : [SPLIT_VOTE, REAL_MAJORITY]), {
          status: 200,
        });
      }
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
      expect(document.activeElement).toBe(screen.getByRole("heading", { name: /Needs your review/ })),
    );
  });

  it("ux-critic finding (P1, live-instrumented): resolving the LAST escalation keeps the aria-live announcement mounted instead of destroying it in the same pass, and moves focus off <body>", async () => {
    let resolved = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/resolve") && init?.method === "POST") {
        resolved = true;
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
      if (url.includes("/escalated")) {
        // Only ONE item pending -- resolving it drops the list to empty,
        // which is exactly the transition that used to unmount the whole
        // panel (live region included) before it could be announced.
        return new Response(JSON.stringify(resolved ? [] : [REAL_MAJORITY]), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<EscalatedPanel checkRunId={5} />);

    fireEvent.click(await screen.findByRole("button", { name: "Accept AI: pass" }));
    fireEvent.change(screen.getByPlaceholderText("Why are you resolving this way?"), {
      target: { value: "AI's grading looks correct on review." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // The heading and the resolved row are both gone (0 items left)...
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: /Needs your review/ })).not.toBeInTheDocument(),
    );
    // ...but the success text is still in the DOM, not discarded along with
    // the panel it used to live inside.
    expect(
      screen.getByText("Resolved as passed. Composite score is now 95%."),
    ).toBeInTheDocument();
    // And focus did not fall back to <body> -- it moved to the surviving
    // live region, the only thing left to hold it.
    await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  });
});
