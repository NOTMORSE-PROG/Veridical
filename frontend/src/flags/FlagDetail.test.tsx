import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { FlagDetailPage } from "./FlagDetail";

const FLAG = {
  id: 5,
  check_result_id: 9,
  check_run_id: 20,
  manuscript_group_label: "Ungrouped",
  check_kind: "citation_integrity",
  criterion_text: null,
  severity: "high",
  confidence: 1.0,
  evidence_excerpt: "Wang, S. (2019). A study of things.",
  page_anchor: "page 34",
  annotation: null,
  overridden: false,
  override_reason: null,
  ai_verdict_summary: "not_supported",
  ai_reasoning: "This source appears in the Retraction Watch database.",
  llm_mode: "real",
};

describe("FlagDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the evidence excerpt, anchor, humanized AI verdict, and severity", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/Wang, S\. \(2019\)/)).toBeInTheDocument();
    expect(screen.getByText("page 34")).toBeInTheDocument();
    expect(screen.getByText("AI verdict: Not supported")).toBeInTheDocument();
    expect(screen.getByText("High severity")).toBeInTheDocument();
    // The D-006 agreement number uses the same "Agreement" vocabulary as
    // screens 4g/4h, never a second, competing "Confidence" word.
    expect(screen.getByText("Agreement 100%")).toBeInTheDocument();
    expect(screen.queryByText(/Confidence/)).not.toBeInTheDocument();
  });

  it("BUG (overflow) regression guard: the evidence blockquote and AI-verdict chip both cap/wrap instead of overflowing on a long real string", async () => {
    const longFlag = {
      ...FLAG,
      evidence_excerpt: "a".repeat(10) + "https://doi.org/10.1234/" + "b".repeat(120),
      ai_verdict_summary: "an_unusually_long_verdict_string_that_a_real_check_detail_blob_could_plausibly_contain",
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": longFlag }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/aaaaaaaaaa/);
    const blockquote = document.querySelector("blockquote");
    expect(blockquote?.className).toContain("break-words");
    const verdictChip = screen.getByText(/AI verdict:/).closest("span");
    expect(verdictChip?.className).toContain("max-w-");
  });

  it("BUG-049: discloses a test-mode (fake-LLM) run so its finding is never mistaken for real", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "fake" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/Test-mode run/)).toBeInTheDocument();
  });

  it("BUG-049: shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("BUG-049 (backend-critic finding): discloses an unknown-mode flag distinctly from real", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "unknown" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/AI mode unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/Test-mode run/)).not.toBeInTheDocument();
  });

  it("builds a breadcrumb back to the report using the manuscript label and check_run_id", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    const link = await screen.findByRole("link", { name: "Ungrouped" });
    expect(link).toHaveAttribute("href", "/report/20");
  });

  it("never renders a disabled Accept-AI-verdict button; Override always stays actionable", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    expect(screen.queryByRole("button", { name: /Accept AI verdict/ })).not.toBeInTheDocument();
    expect(
      screen.getByText("This finding stands as VERIDICAL reported it unless you override it below."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Override" })).not.toBeDisabled();
  });

  it("requires a reason before confirming, validated on click (Confirm is never pre-disabled)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    const confirm = await screen.findByRole("button", { name: "Confirm override" });
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);

    expect(await screen.findByText("Enter a reason before confirming.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Why are you overriding this finding?")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("submits the override with the reason and shows the AI finding + the instructor's own reason side by side afterward", async () => {
    const overriddenFlag = {
      ...FLAG,
      overridden: true,
      override_reason: "Checked myself, not actually retracted.",
      report: { check_run_id: 20, results: [], status: "ready" },
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": FLAG, "/flags/5/override": overriddenFlag }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    fireEvent.change(await screen.findByPlaceholderText("Why are you overriding this finding?"), {
      target: { value: "Checked myself, not actually retracted." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm override" }));

    const banner = await screen.findByText(/You overrode this finding\./);
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(/Checked myself, not actually retracted\./)).toBeInTheDocument();
    // Original AI finding still shown, never destroyed (ticket AC):
    expect(screen.getAllByText(/Not supported/).length).toBeGreaterThan(0);
    expect(
      screen.getByRole("link", { name: "View updated report" }),
    ).toHaveAttribute("href", "/report/20");
    // BUG (focus dropped to <body> after submit) regression guard:
    // focus must land on the terminal banner, not get stranded.
    expect(document.activeElement).toBe(banner.closest('[tabindex="-1"]'));
  });

  it("BUG (focus/announcement) regression guard: Cancel returns focus to the Override button, not <body>", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    const overrideButton = screen.getByRole("button", { name: "Override" });
    fireEvent.click(overrideButton);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("button", { name: "Override" })).toHaveFocus();
  });

  it("saves an annotation, validated on click, with a real status confirmation", async () => {
    const annotated = { ...FLAG, annotation: "Confirmed with the adviser." };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": FLAG, "/flags/5/annotate": annotated }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    const box = screen.getByRole("textbox", { name: /Annotation/i });
    fireEvent.change(box, { target: { value: "Confirmed with the adviser." } });
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    const status = await screen.findByText("Saved.");
    expect(status).toHaveAttribute("role", "status");
  });

  it("shows an error when saving an empty annotation, instead of silently doing nothing", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    expect(await screen.findByText("Enter a note before saving.")).toBeInTheDocument();
  });

  it("shows a real loading state, then a real fetch-error state with a working retry", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) return new Response("Internal error", { status: 500 });
        return new Response(JSON.stringify(FLAG), { status: 200 });
      }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Try again");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText(/Wang, S\./)).toBeInTheDocument());
  });

  it("shows a distinct not-found error message when the flag doesn't exist", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/flags/5": new Response(
          JSON.stringify({ error: { code: "not_found", message: "No flag 5." } }),
          { status: 404 },
        ),
      }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByRole("alert")).toHaveTextContent("No flag 5.");
  });
});
