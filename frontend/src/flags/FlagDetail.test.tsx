import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConfirmCitationSourceOut, FlagOut, ReportOut } from "../api/types";
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
  // BUG-078: this fixture's ai_verdict_summary ("not_supported") is never
  // "unverifiable_not_found", so the new "Verify this source" section
  // never gates on for any test below -- these two fields just need to be
  // present to satisfy FlagOut's shape.
  citation_source_key: null,
  confirmed_citation_source: false,
};

describe("FlagDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the evidence excerpt, anchor, humanized AI verdict, and severity", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/Wang, S\. \(2019\)/)).toBeInTheDocument();
    expect(screen.getByText("page 34")).toBeInTheDocument();
    expect(screen.getByText("AI suggestion")).toBeInTheDocument();
    expect(screen.getByText("Not supported")).toBeInTheDocument();
    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.queryByText("Agreement 100%")).not.toBeInTheDocument();
    expect(screen.getByText(/not shown as a percentage/)).toBeInTheDocument();
  });

  it("maps an internal agreement kind to instructor-facing problem copy", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "internal_agreement",
        ai_verdict_summary: "agreement_unmatched_intent",
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByText("Possible objective-to-outcome gap")).toBeInTheDocument();
    expect(screen.queryByText("Agreement unmatched intent")).not.toBeInTheDocument();
  });

  it("removes a legacy reuse percentage from system reasoning but preserves passage evidence", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_exact_duplicate_passage",
        evidence_excerpt: "The recorded process completed 100% of the planned cases.",
        ai_reasoning: "This passage appears to be a duplicate or near-duplicate (100.0% match) of archived manuscript #34.",
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByText(/completed 100% of the planned cases/)).toBeInTheDocument();
    expect(screen.getByText(/duplicate or near-duplicate of archived manuscript #34/i)).toBeInTheDocument();
    expect(screen.queryByText(/100\.0% match/)).not.toBeInTheDocument();
  });

  it("BUG-154: renders the side-by-side passage comparison when the API returns one, instead of leaving the instructor with only a link into the (previously broken) document viewer", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_exact_duplicate_passage",
        page_anchor: "p. 31",
        passage_pair: {
          own_excerpt: "The system uses a hybrid rule-based and AI approach.",
          own_context_before: "Chapter 3 begins here.",
          own_context_after: "It continues below.",
          matched_ref: 3,
          matched_excerpt: "The system uses a hybrid rule-based and AI approach.",
          matched_context_before: "Prior context.",
          matched_context_after: "Later context.",
          context_words_each_side: 60,
          similarity: 1.0,
          level: "exact_duplicate",
        },
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByText("Passage comparison", { selector: "h3" })).toBeInTheDocument();
    expect(screen.getByText("Your manuscript · p. 31")).toBeInTheDocument();
    expect(screen.getByText("Archived manuscript #3")).toBeInTheDocument();
    expect(screen.getAllByText(/hybrid rule-based and AI approach/)).toHaveLength(2);
  });

  it("shows no passage comparison when the API returns none (the ordinary, non-reuse case)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText("Passage comparison", { selector: "h3" })).not.toBeInTheDocument();
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
    expect(blockquote?.className).toContain("signal-evidence-quote");
    const verdictRecord = screen.getByText(/An unusually long verdict/).closest("p");
    expect(verdictRecord?.className).toContain("signal-ai-suggestion");
  });

  it("BUG-049: discloses a test-mode (fake-LLM) run so its finding is never mistaken for real", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "fake" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("Test-mode AI result")).toBeInTheDocument();
  });

  it("BUG-049: shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText("Test-mode AI result")).not.toBeInTheDocument();
  });

  it("BUG-049 (backend-critic finding): discloses an unknown-mode flag distinctly from real", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "unknown" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("AI mode could not be verified")).toBeInTheDocument();
    expect(screen.queryByText("Test-mode AI result")).not.toBeInTheDocument();
  });

  it("BUG-097: discloses a first-upload-context flag distinctly from an ordinary one", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, first_upload_context: true } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("Limited first-upload comparison context")).toBeInTheDocument();
  });

  it("BUG-097: shows no first-upload-context disclosure for an ordinary flag", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, first_upload_context: false } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText("Limited first-upload comparison context")).not.toBeInTheDocument();
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
    expect(screen.getByText("This finding remains open unless you record a reasoned override.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Override" })).not.toBeDisabled();
  });

  it("BUG-053: never claims a finding stands as reported when no AI verdict actually exists", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": { ...FLAG, ai_verdict_summary: null } }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    expect(
      screen.queryByText("This finding remains open unless you record a reasoned override."),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "VERIDICAL did not reach a determination, so this finding does not affect readiness unless you affirm it.",
      ),
    ).toBeInTheDocument();
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
    expect(screen.getByRole("textbox", { name: "Reason (required)" })).toHaveAttribute(
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
    fireEvent.change(await screen.findByRole("textbox", { name: "Reason (required)" }), {
      target: { value: "Checked myself, not actually retracted." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm override" }));

    const banner = await screen.findByText(/You overrode this possible inconsistency/);
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

  it("BUG-069 item 4: an overridden flag's own header shows an Overridden pill, matching the report's flags list", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/flags/5": { ...FLAG, overridden: true, override_reason: "Checked myself." },
      }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);

    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.getByText("Overridden")).toBeInTheDocument();
  });

  it("no Overridden pill in the header for a live, unresolved flag", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);

    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.queryByText("Overridden")).not.toBeInTheDocument();
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
    const status = await screen.findByText("Annotation saved.");
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

// BUG-078: "Confirm this source" -- gating (citation_integrity +
// unverifiable_not_found only), the confirmable-vs-nothing-to-confirm
// split within that, the modal's required-reason validation, and the
// three-way terminal state (not resolved / ordinary override / confirmed
// source) rendering a DIFFERENT, honest banner and announcement for each.
function makeFlag(overrides: Partial<FlagOut> = {}): FlagOut {
  return {
    id: 20,
    check_result_id: 5,
    check_run_id: 44,
    manuscript_group_label: "VERIDICAL",
    check_kind: "citation_integrity",
    criterion_text: null,
    severity: "low",
    confidence: null,
    evidence_excerpt: "Serquiña, R. (2025). Automated integrity checking. J. Ed. Tech, 12(3).",
    page_anchor: "reference #3",
    annotation: null,
    overridden: false,
    override_reason: null,
    ai_verdict_summary: "unverifiable_not_found",
    ai_reasoning: null,
    llm_mode: "real",
    passage_pair: null,
    first_upload_context: false,
    evidence_unavailable: false,
    citation_source_key: { kind: "doi", value: "10.9999/local-source" },
    confirmed_citation_source: false,
    ...overrides,
  };
}

// Not fetched by FlagDetailPage itself (only surfaces via the "View
// updated report" link's href) -- just needs to satisfy
// ConfirmCitationSourceOut's `report: ReportOut` field shape.
const REPORT: ReportOut = {
  check_run_id: 44,
  manuscript_group_label: "VERIDICAL",
  manuscript_original_filename: null,
  rubric_title: "TIP Format",
  status: "conditionally_ready",
  composite_score: 82,
  thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: null,
  flag_deduction: 0,
  unresolved_high_flag_count: 0,
  decision: null,
  decided_at: null,
  decision_note: null,
  pending_review_count: 0,
  rubric_is_current: true,
  llm_mode: "real",
  rubric_needs_review: false,
  rubric_parse_issues: null,
  previous_status: null,
  previous_composite_score: null,
  integrity_check_status: [],
  results: [],
};

function renderFlag(flag: FlagOut, extraHandlers: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", stubFetchByPath({ "/flags/20": flag, ...extraHandlers }));
  return renderWithProviders(<FlagDetailPage />, {
    route: "/flags/20",
    path: "/flags/:flagId",
  });
}

describe("FlagDetail — BUG-078 confirm-source", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the confirmable copy and button for a citation flag with a real key", async () => {
    renderFlag(makeFlag());
    expect(await screen.findByText("Verified the source yourself?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm this source" })).toBeInTheDocument();
    // Override stays available alongside source confirmation.
    expect(screen.getByRole("button", { name: "Override" })).toBeInTheDocument();
  });

  it("shows the nothing-to-confirm explanation, no button, when there's no key", async () => {
    renderFlag(makeFlag({ citation_source_key: null }));
    expect(await screen.findByText("No source record can be confirmed")).toBeInTheDocument();
    expect(screen.getByText(/could not identify a DOI, ISBN, or title/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm this source" })).not.toBeInTheDocument();
    // Override still offered as the fallback path the copy names.
    expect(screen.getByRole("button", { name: "Override" })).toBeInTheDocument();
  });

  it("never shows the section for a non-citation-integrity flag", async () => {
    renderFlag(
      makeFlag({
        check_kind: "statistical_forensics",
        ai_verdict_summary: "grim_inconsistent",
        citation_source_key: null,
      }),
    );
    await screen.findByRole("button", { name: "Override" });
    expect(screen.queryByText("Verified the source yourself?")).not.toBeInTheDocument();
  });

  it("never shows the section for a citation flag with a different verdict (e.g. retracted)", async () => {
    renderFlag(makeFlag({ ai_verdict_summary: "retracted_source", citation_source_key: null }));
    await screen.findByRole("button", { name: "Override" });
    expect(screen.queryByText("Verified the source yourself?")).not.toBeInTheDocument();
  });

  it("opening the modal requires a reason before it will submit", async () => {
    renderFlag(makeFlag());
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Confirm this source is legitimate?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm this source" }));

    expect(
      await screen.findByText("Enter where you verified this before confirming."),
    ).toBeInTheDocument();
  });

  it("caps the evidence blockquote so a long citation can't push the disclosure/buttons off-screen", async () => {
    // ux-critic (BUG-078 review), live-reproduced: an unbounded blockquote
    // on a genuinely long evidence_excerpt pushed the mandatory
    // disclosure, reason field, and Confirm/Cancel buttons out of view.
    renderFlag(makeFlag({ evidence_excerpt: "A very long citation. ".repeat(60) }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Confirm this source is legitimate?",
    });
    const blockquote = within(dialog).getByText(/A very long citation\./);
    expect(blockquote.className).toContain("max-h-");
    expect(blockquote.className).toContain("overflow-y-auto");
  });

  it("shows a distinct caution note for a title-keyed (non-unique) match", async () => {
    renderFlag(makeFlag({ citation_source_key: { kind: "title", value: "a common thesis title" } }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    expect(await screen.findByText(/Matched by title, not a DOI or ISBN/)).toBeInTheDocument();
    // The raw casefolded key value is deliberately not shown for a title match.
    expect(screen.queryByText(/a common thesis title/)).not.toBeInTheDocument();
  });

  it("confirming resolves the flag with a distinct, honest banner — never the override wording", async () => {
    const confirmed: ConfirmCitationSourceOut = {
      ...makeFlag({
        overridden: true,
        confirmed_citation_source: true,
        override_reason: "Verified on the publisher's own website.",
      }),
      report: REPORT,
    };
    renderFlag(makeFlag(), {
      "/flags/20/confirm-source": confirmed,
    });

    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Confirm this source is legitimate?",
    });
    fireEvent.change(screen.getByLabelText("Where you verified this (required)"), {
      target: { value: "Verified on the publisher's own website." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm this source" }));

    expect(
      await screen.findByText(/You confirmed the source after checking it/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Verified on the publisher's own website\./)).toBeInTheDocument();
    expect(
      screen.getByText(/verified-source record also applies/),
    ).toBeInTheDocument();
    // The ordinary-override sentence must never appear on this path.
    expect(screen.queryByText(/You overrode this possible inconsistency/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Source confirmed. The readiness band was recalculated.",
      ),
    ).toBeInTheDocument();

    await waitFor(() => expect(document.activeElement).toHaveAttribute("tabindex", "-1"));
  });

  it("an ordinary override still renders the original override banner, not the confirm one", async () => {
    renderFlag(
      makeFlag({
        overridden: true,
        confirmed_citation_source: false,
        override_reason: "Not actually retracted.",
      }),
    );
    expect(await screen.findByText(/You overrode this possible inconsistency/)).toBeInTheDocument();
    expect(screen.queryByText(/You confirmed the source after checking it/)).not.toBeInTheDocument();
  });
});
