import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConfirmCitationSourceOut, FlagOut, ReportOut } from "../api/types";
import { renderWithProviders, stubFetchByPath as baseStubFetchByPath } from "../test/renderWithProviders";
import { FlagDetailPage } from "./FlagDetail";
import { CITATION_COMPARISON } from "../test/citationComparisonFixture";

const FLAG = {
  assessment_revision: 0,
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
  is_passage_level: false,
  // BUG-078: this fixture's ai_verdict_summary ("not_supported") is never
  // "unverifiable_not_found", so the new "Verify this source" section
  // never gates on for any test below -- these two fields just need to be
  // present to satisfy FlagOut's shape.
  citation_source_key: null,
  confirmed_citation_source: false,
};

function stubFetchByPath(handlers: Record<string, unknown>) {
  return baseStubFetchByPath({ "/check-runs/20/report": { ...REPORT, check_run_id: 20 },
    "/check-runs/44/report": REPORT, ...handlers });
}

describe("FlagDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("BUG-435: warns about test AI responses without attributing a local F7 comparison to fixtures", async () => {
    renderFlag(makeFlag({ check_kind: "originality_reuse", llm_mode: "fake", is_passage_level: true,
      affects_readiness: false, citation_source_key: null, ai_verdict_summary: "possible_reuse",
      ai_reasoning: "Recorded local comparison evidence.", passage_pair: {
        matched_ref: 2, own_excerpt: "Stored local passage alpha.", matched_excerpt: "Stored comparison passage beta.",
        own_context_before: null, own_context_after: null, matched_context_before: null, matched_context_after: null,
        similarity: 0.9, level: "high_similarity", context_words_each_side: 10,
      } }));
    expect(await screen.findByText("Test-mode AI responses")).toBeVisible();
    expect(screen.getByText("This run is recorded in test AI mode. AI responses use fixtures instead of real AI grading. Do not treat AI-derived judgments as a real assessment of the manuscript. Check the recorded evidence directly.")).toBeVisible();
    expect(screen.queryByText(/This finding came from fixture responses/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Review lead: not scored" })).toBeVisible();
    expect(screen.getByText("Stored local passage alpha.")).toBeInTheDocument();
    expect(screen.getByText("Stored comparison passage beta.")).toBeInTheDocument();
  });

  it("BUG-435: retains the distinct saved-registry test-mode caution", async () => {
    renderFlag(makeFlag({ llm_mode: "fake", ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: CITATION_COMPARISON }));
    expect(await screen.findByText("Check recorded in a test-mode run")).toBeVisible();
    expect(screen.getByText(/Do not treat its saved provider values as evidence of a fresh external lookup/)).toBeVisible();
    expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Recorded citation metadata comparison" })).toBeInTheDocument();
  });

  it.each([true, false])("qualifies saved title findings only when the API reports missing parsing proof (%s)", async (warning) => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: CITATION_COMPARISON, citation_metadata_review_warning: warning ? "quoted_title_projection_unverified" : null } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText("Registry check result");
    expect(!!screen.queryByText("Historical title comparison needs review")).toBe(warning);
    expect(screen.getByText(warning ? "Fields marked different by the saved check: Title, Year, First author." : "Fields marked different: Title, Year, First author.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: warning ? "Original recorded explanation" : "Recorded reasoning" })).toBeInTheDocument();
    expect(screen.getByText(FLAG.ai_reasoning)).toBeInTheDocument();
  });

  it.each(["complete", "partial", "legacy"])("V-081: exposes saved registry evidence or its absence (%s)", async (state) => {
    const comparison = state === "legacy" ? null : state === "partial"
      ? { ...CITATION_COMPARISON, registered: { ...CITATION_COMPARISON.registered, year: null, first_author: null } }
      : CITATION_COMPARISON;
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "unknown", ai_verdict_summary: "possible_metadata_mismatch", citation_metadata_comparison: comparison } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("Registry check result")).toBeInTheDocument();
    expect(screen.queryByText("AI suggestion")).not.toBeInTheDocument();
    expect(screen.queryByText("AI mode could not be verified")).not.toBeInTheDocument();
    if (comparison) {
      const table = screen.getByRole("table", { name: "Recorded citation metadata comparison" });
      expect(within(table).getByText("Cited study title")).toBeInTheDocument();
      expect(within(table).getByText("Registered study title")).toBeInTheDocument();
      expect(within(table).getByRole("columnheader", { name: "Returned by Crossref" })).toBeInTheDocument();
      expect(screen.getByText("abc-saved-policy-hash")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open DOI source (opens in a new tab)" })).toHaveAttribute("href", CITATION_COMPARISON.doi_source_url);
      if (state === "partial") expect(within(table).getAllByText("Not recorded")).toHaveLength(2);
    } else {
      expect(screen.getByText("Provider comparison was not saved")).toBeInTheDocument();
      expect(screen.getByText(/This historical finding has no saved provider comparison/)).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryByText("Registered study title")).not.toBeInTheDocument();
    }
  });

  it("keeps the AI label and mode disclosure for an actual semantic finding", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, check_kind: "semantic", llm_mode: "unknown" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("AI suggestion")).toBeInTheDocument();
    expect(screen.getByText("AI mode could not be verified")).toBeInTheDocument();
  });

  it("renders the same stored agreement pair as the manuscript viewer", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, check_kind: "internal_agreement", agreement_pair: { intent_text: "To build a login module.", intent_anchor: "page 1", outcome_text: "The login module was not implemented.", outcome_anchor: "page 2" }, affects_readiness: false } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("Statement selected as an objective")).toBeInTheDocument();
    expect(screen.getByText("Statement selected as a result")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open result location: page 2" })).toHaveAttribute("href", "/report/20/document?finding=5&anchor=page%202");
  });

  it("shows evidence and a neutral method when the stored method is unknown", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/Wang, S\. \(2019\)/)).toBeInTheDocument();
    expect(screen.getByText("page 34")).toBeInTheDocument();
    expect(screen.getByText("Recorded check result")).toBeInTheDocument();
    expect(screen.getByText("Not supported")).toBeInTheDocument();
    expect(screen.getByText("High severity")).toBeInTheDocument();
    expect(screen.queryByText("Agreement 100%")).not.toBeInTheDocument();
    expect(screen.getByText(/Review the recorded evidence and source/)).toBeInTheDocument();
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
        // BUG-170: a passage-level flag's evidence_excerpt is real
        // manuscript text and must still render as a quote -- this fixture
        // predates `is_passage_level` (added to FlagOut this ticket) and
        // needs it set explicitly since the backend always derives it from
        // the same "_passage" suffix `ai_verdict_summary` already has here.
        is_passage_level: true,
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
    expect(screen.getByText("Your manuscript excerpt")).toBeInTheDocument();
    expect(screen.getByText("Stored comparison excerpt · Library record #3")).toBeInTheDocument();
    expect(screen.getAllByText(/hybrid rule-based and AI approach/)).toHaveLength(2);
  });

  it("shows no passage comparison when the API returns none (the ordinary, non-reuse case)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText("Passage comparison", { selector: "h3" })).not.toBeInTheDocument();
  });

  it("BUG-170: never renders a whole-document reuse flag's system-authored summary sentence as a manuscript quote", async () => {
    // evidence_excerpt here is the same templated accusation sentence
    // real F7 whole-document flags carry -- never a real quote, unlike a
    // passage-level flag's. Rendering it inside <blockquote>/<cite> would
    // present it as checkable manuscript text when it isn't (the exact
    // defect this ticket names). A real passage_pair is attached (BUG-153),
    // so the section keeps its "Evidence from the manuscript" heading --
    // real evidence still exists, just not as this sentence.
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_high_similarity",
        is_passage_level: false,
        evidence_excerpt: "This manuscript shows high textual similarity to archived manuscript #3 in VERIDICAL's shared originality library: possible shared content or reuse. Please verify manually.",
        ai_reasoning: null,
        page_anchor: "whole document",
        passage_pair: {
          own_excerpt: "Chapter 3: Methodology text.",
          own_context_before: null,
          own_context_after: null,
          matched_ref: 3,
          matched_excerpt: "Chapter 3: Research Methodology text.",
          matched_context_before: null,
          matched_context_after: null,
          context_words_each_side: 60,
          similarity: 0.91,
          level: "high_similarity",
        },
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByRole("heading", { name: "Evidence from the manuscript" })).toBeInTheDocument();
    expect(document.querySelector("blockquote.signal-evidence-quote")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recorded reasoning" })).toBeInTheDocument();
    expect(screen.getByText(/possible shared content or reuse/)).toBeInTheDocument();
    expect(screen.getByText("Passage comparison", { selector: "h3" })).toBeInTheDocument();
  });

  it("BUG-170: labels a same-instructor resubmission honestly when no passage comparison ever applies", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_same_instructor_resubmission",
        is_passage_level: false,
        severity: "low",
        evidence_excerpt: "This manuscript appears to be the same document as your own earlier upload, archived manuscript #4.",
        ai_reasoning: null,
        page_anchor: "whole document",
        passage_pair: null,
        evidence_unavailable: false,
        matched_ref: 4,
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByRole("heading", { name: "No manuscript excerpt is available" })).toBeInTheDocument();
    // V-077 (UX review P1-3): the matched record can be opened and verified,
    // not only agreed with.
    expect(screen.getByRole("link", { name: /Open Library record #4/ })).toHaveAttribute("href", "/library/4");
    expect(document.querySelector("blockquote.signal-evidence-quote")).not.toBeInTheDocument();
    expect(screen.getByText("No passage-level comparison applies")).toBeInTheDocument();
    expect(screen.getByText(/no specific passage to compare side by side/)).toBeInTheDocument();
    // Never claims a search failed -- none was ever applicable here.
    expect(screen.queryByText(/did not find one close enough/)).not.toBeInTheDocument();
  });

  it("BUG-170: says severity was lowered only for the genuine BUG-153 unevidenceable-match downgrade, not the resubmission case", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({
      "/flags/5": {
        ...FLAG,
        check_kind: "originality_reuse",
        ai_verdict_summary: "reuse_high_similarity_chapter",
        is_passage_level: false,
        severity: "med",
        evidence_excerpt: "This manuscript's Chapter 2 shows high textual similarity to a chapter in archived manuscript #9: possible shared content or reuse. Please verify manually.",
        ai_reasoning: null,
        page_anchor: "Chapter 2",
        passage_pair: null,
        evidence_unavailable: true,
      },
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });

    expect(await screen.findByRole("heading", { name: "No manuscript excerpt is available" })).toBeInTheDocument();
    expect(screen.getByText("No supporting passage could be found")).toBeInTheDocument();
    expect(screen.getByText(/severity was lowered because no matching passage could be shown/)).toBeInTheDocument();
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
    expect(await screen.findByText("Test-mode AI responses")).toBeInTheDocument();
  });

  it("BUG-049: shows no test-mode disclosure for a real run", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\. \(2019\)/);
    expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
  });

  it("BUG-049 (backend review finding): discloses an unknown-mode flag distinctly from real", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": { ...FLAG, llm_mode: "unknown" } }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText("AI mode could not be verified")).toBeInTheDocument();
    expect(screen.queryByText("Test-mode AI responses")).not.toBeInTheDocument();
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

  it("requires a reason after the current assessment loads, validated on click", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId", reviewRunId: 20 });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    const confirm = await screen.findByRole("button", { name: "Confirm override" });
    await waitFor(() => expect(confirm).toBeEnabled());
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
      assessment_revision: 1,
      overridden: true,
      override_reason: "Checked myself, not actually retracted.",
      report: { ...REPORT, assessment_revision: 1, check_run_id: 20, results: [], status: "ready" },
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": FLAG, "/flags/5/override": overriddenFlag }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId", reviewRunId: 20 });
    await screen.findByText(/Wang, S\./);
    screen.getByRole("button", { name: "Override" }).focus();
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
    expect(screen.getByRole("heading", { name: "Finding overridden" })).toHaveFocus();
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
    overrideButton.focus();
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
    assessment_revision: 0,
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
    is_passage_level: false,
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
  assessment_revision: 0,
  check_run_id: 44,
  manuscript_id: 44,
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
    reviewRunId: flag.check_run_id,
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
      name: "Confirm this source exists?",
    });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Confirm this source" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm this source" }));

    expect(
      await screen.findByText("Enter where you verified this before confirming."),
    ).toBeInTheDocument();
  });

  it("caps the evidence blockquote so a long citation can't push the disclosure/buttons off-screen", async () => {
    // UX review (BUG-078 review), live-reproduced: an unbounded blockquote
    // on a genuinely long evidence_excerpt pushed the mandatory
    // disclosure, reason field, and Confirm/Cancel buttons out of view.
    renderFlag(makeFlag({ evidence_excerpt: "A very long citation. ".repeat(60) }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Confirm this source exists?",
    });
    const blockquote = within(dialog).getByText(/A very long citation\./);
    expect(blockquote.className).toContain("max-h-");
    expect(blockquote.className).toContain("overflow-y-auto");
  });

  it("shows a distinct caution note for a title-keyed (non-unique) match", async () => {
    renderFlag(makeFlag({ citation_source_key: { kind: "title", value: "a common thesis title" } }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    expect(await screen.findByText(/Check that this is the reference cited in the manuscript/)).toBeInTheDocument();
    // The current source key is available for deliberate reference verification.
    expect(screen.getByText(/a common thesis title/)).toBeVisible();
  });

  it("confirming resolves the flag with a distinct, honest banner — never the override wording", async () => {
    const confirmed: ConfirmCitationSourceOut = {
      ...makeFlag({
        assessment_revision: 1,
        overridden: true,
        confirmed_citation_source: true,
        override_reason: "Verified on the publisher's own website.",
      }),
      report: { ...REPORT, assessment_revision: 1 },
    };
    renderFlag(makeFlag(), {
      "/flags/20/confirm-source": confirmed,
    });

    fireEvent.click(await screen.findByRole("button", { name: "Confirm this source" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Confirm this source exists?",
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
      screen.getByText(/only to this exact reference in your account, not to another reference or instructor/),
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

it.each(["Override", "Confirm this source"].flatMap(action => [503, 403].map(status => ({ action, status }))))(
  "preserves the $action editor on transient read failure and removes inaccessible evidence ($status)",
  async ({ action, status }) => {
    let failFlag = false; let revision = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/flags/20") return new Response(JSON.stringify(failFlag
        ? { error: { code: "internal", message: "Controlled current-read failure" } }
        : makeFlag({ assessment_revision: revision })), { status: failFlag ? status : 200 });
      if (path === "/check-runs/44/report") return new Response(JSON.stringify({ ...REPORT, assessment_revision: 1 }));
      if (path.endsWith("/escalated")) return new Response("[]");
      return new Response(JSON.stringify({ instructor: { id: 1 } }));
    }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/20", path: "/flags/:flagId", reviewRunId: 44 });
    fireEvent.click(await screen.findByRole("button", { name: action }));
    const dialog = screen.getByRole("dialog"); const editor = within(dialog).getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Exact unsaved source verification reason" } });
    failFlag = true;
    fireEvent.click(within(dialog).getByRole("button", { name: "Review current assessment" }));
    if (status === 403) {
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByText("Could not load this evidence")).toBeVisible();
      expect(screen.queryByText(makeFlag().evidence_excerpt)).not.toBeInTheDocument(); return;
    }
    await within(dialog).findByText(/current finding and assessment could not be loaded together/);
    expect(screen.getByRole("dialog")).toBe(dialog); expect(editor.isConnected).toBe(true);
    expect(editor).toHaveValue("Exact unsaved source verification reason");
    const submit = within(dialog).getByRole("button", { name: action === "Override" ? "Confirm override" : "Confirm this source" });
    expect(submit).toBeDisabled(); failFlag = false; revision = 1;
    fireEvent.click(within(dialog).getByRole("button", { name: "Review current assessment" }));
    const rebind = await within(dialog).findByRole("button", { name: "Use my reason with this current assessment" });
    expect(submit).toBeDisabled(); fireEvent.click(rebind);
    await waitFor(() => expect(submit).toBeEnabled());
    expect(editor).toHaveValue("Exact unsaved source verification reason");
  },
);
