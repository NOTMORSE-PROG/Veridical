import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExcludedReuseMatchOut, FlagSummaryOut } from "../api/types";
import { useExcludedReuseMatches } from "../report/useReport";
import { renderWithProviders } from "../test/renderWithProviders";
import { ReuseExplorePanel } from "./ReuseExplorePanel";
import { SignalDocumentViewerPage } from "./SignalDocumentViewer";

vi.mock("./PdfPane", () => ({ PdfPane: () => <div>PDF source control</div> }));
vi.mock("./DocxPane", () => ({ DocxPane: () => <div>DOCX source control</div> }));

type Query = ReturnType<typeof useExcludedReuseMatches>;
type Props = ComponentProps<typeof ReuseExplorePanel>;
const FLAG: FlagSummaryOut = {
  id: 7, check_kind: "originality_reuse", severity: "med", criterion_text: null,
  evidence_excerpt: "Recorded source-bound passage.", page_anchor: "¶0",
  overridden: false, is_passage_level: true, first_upload_context: false,
  confirmed_citation_source: false, problem_kind: null, affects_readiness: false,
};
const MATCH: ExcludedReuseMatchOut = {
  id: "same-key-match", own_excerpt: "Stored optional passage.", own_context_before: null,
  own_context_after: null, own_region: { flag_id: -1, kind: "paragraph_only", page: null,
    end_page: null, bbox: null, all_bboxes: [], paragraph: 0, index: null },
  matched_ref: 2, matched_excerpt: "Stored comparison passage.", matched_context_before: null,
  matched_context_after: null, context_words_each_side: 10, similarity: 0.9,
  level: "high_similarity", excluded_reason: ["reference_list"],
};

function query(overrides: Partial<{ data: Query["data"]; isFetching: boolean; isPending: boolean; isError: boolean; isSuccess: boolean; refetch: () => unknown }> = {}): Query {
  return {
    data: { passage_archive_size_n: 4, matches: [] }, isFetching: false,
    isPending: false, isError: false, isSuccess: true, refetch: vi.fn(), ...overrides,
  } as Query;
}
function props(overrides: Partial<Props> = {}): Props {
  return {
    flags: [FLAG], flagsFetching: false, flagsError: false, flagsSucceeded: true,
    onRetryFlags: vi.fn(), onSelectFlag: vi.fn(), onBack: vi.fn(),
    includeReferenceList: false, includeBlockQuote: false,
    onToggleReferenceList: vi.fn(), onToggleBlockQuote: vi.fn(),
    excludedMatchesQuery: query(), expandedMatchId: null, onExpandMatch: vi.fn(),
    ...overrides,
  };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("V-081: passage comparison display (BUG-434)", () => {
  it.each([0, 1, 4])("counts %s stored comparison passages without asserting Library manuscript count", (count) => {
    render(<ReuseExplorePanel {...props({ excludedMatchesQuery: query({ data: { passage_archive_size_n: count, matches: [] } }) })} />);
    expect(screen.getByText(`Comparison coverage: ${count} stored comparison passage${count === 1 ? "" : "s"}. This counts passages, not Library manuscripts.`)).toBeVisible();
    expect(screen.getByText("An empty result is not evidence of originality.")).toBeVisible();
    expect(screen.queryByText(/Library currently holds/)).not.toBeInTheDocument();
  });

  it("uses source ordinals, preserves page anchors and limits the non-scoring claim to explicit metadata", () => {
    const onSelectFlag = vi.fn();
    render(<ReuseExplorePanel {...props({ onSelectFlag, flags: [FLAG, { ...FLAG, id: 8, page_anchor: "¶2", affects_readiness: undefined }, { ...FLAG, id: 9, page_anchor: "p. 4", affects_readiness: true }] })} />);
    expect(screen.getByText("Recorded passage matches")).toBeVisible();
    expect(screen.getByText("Extracted paragraph 1")).toBeVisible();
    expect(screen.getByText("Extracted paragraph 3")).toBeVisible();
    expect(screen.getByText("p. 4")).toBeVisible();
    expect(screen.getAllByText("Review lead: not scored")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Extracted paragraph 3/ }));
    expect(onSelectFlag).toHaveBeenCalledWith(8);
    expect(FLAG.page_anchor).toBe("¶0");
  });

  it.each([false, true])("an optional-query failure outranks cached coverage and has one retry (toggles=%s)", (enabled) => {
    render(<ReuseExplorePanel {...props({ includeReferenceList: enabled, excludedMatchesQuery: query({ isError: true, isSuccess: false }) })} />);
    expect(screen.getByText(enabled ? "Comparison coverage and additional matches could not be loaded." : "Comparison coverage could not be loaded.")).toBeVisible();
    expect(screen.queryByText(/Comparison coverage: 4/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No additional matches found/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Try again" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Recorded source-bound passage/ })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats a cached active refetch as loading rather than completed empty results", () => {
    render(<ReuseExplorePanel {...props({ includeBlockQuote: true, excludedMatchesQuery: query({ isFetching: true, isError: true, isSuccess: false }) })} />);
    expect(screen.getByText("Loading comparison coverage and additional matches.")).toBeVisible();
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Comparison coverage: 4|No additional matches found/)).not.toBeInTheDocument();
    expect(screen.getByText("Loading comparison coverage and additional matches.").closest("[role='status']")).toHaveAttribute("aria-busy", "true");
  });

  it("does not mistake a disabled pending query for an active request", () => {
    render(<ReuseExplorePanel {...props({ excludedMatchesQuery: query({ data: undefined, isPending: true, isSuccess: false }) })} />);
    expect(screen.getByText("Comparison coverage is unavailable.")).toBeVisible();
    expect(screen.queryByText(/Loading comparison coverage/)).not.toBeInTheDocument();
  });

  it.each(["fetching", "error", "unavailable"] as const)("does not claim an empty recorded result while its independent request is %s", (state) => {
    render(<ReuseExplorePanel {...props({ flags: undefined, flagsFetching: state === "fetching", flagsError: state === "error", flagsSucceeded: false })} />);
    expect(screen.queryByText("No passage-level match was recorded for this check.")).not.toBeInTheDocument();
    expect(screen.getByText("Comparison coverage: 4 stored comparison passages. This counts passages, not Library manuscripts.")).toBeVisible();
    if (state === "error") expect(screen.getByRole("button", { name: "Retry loading recorded matches" })).toBeVisible();
  });

  it("permits a recorded-empty statement only for a settled successful request", () => {
    const view = render(<ReuseExplorePanel {...props({ flags: [] })} />);
    expect(screen.getByText("No passage-level match was recorded for this check.")).toBeVisible();
    view.rerender(<ReuseExplorePanel {...props({ flags: [], flagsFetching: true })} />);
    expect(screen.queryByText("No passage-level match was recorded for this check.")).not.toBeInTheDocument();
  });

  it.each(["fetching", "error"] as const)("retains focused recorded cached rows during their own %s", (state) => {
    const view = render(<ReuseExplorePanel {...props()} />);
    const record = screen.getByRole("button", { name: /Recorded source-bound passage/ });
    record.focus();
    view.rerender(<ReuseExplorePanel {...props({ flagsFetching: state === "fetching", flagsError: state === "error", flagsSucceeded: state !== "error" })} />);
    expect(record).toHaveFocus();
    expect(screen.getByText("Showing the last loaded recorded matches.")).toBeVisible();
  });

  it.each(["fetching", "error"] as const)("retains focused same-query optional rows during %s without a fresh success claim", (state) => {
    const data = { passage_archive_size_n: 4, matches: [MATCH] };
    const view = render(<ReuseExplorePanel {...props({ includeReferenceList: true, excludedMatchesQuery: query({ data }) })} />);
    const row = screen.getByRole("button", { name: /Stored optional passage/ }); row.focus();
    view.rerender(<ReuseExplorePanel {...props({ includeReferenceList: true, excludedMatchesQuery: query({ data, isFetching: state === "fetching", isError: state === "error", isSuccess: state !== "error" }) })} />);
    expect(row).toHaveFocus();
    expect(screen.getByText("Showing the last loaded additional matches.")).toBeVisible();
    expect(screen.queryByText(/Comparison coverage: 4/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No additional matches found/)).not.toBeInTheDocument();
  });

  it.each(["optional", "recorded"] as const)("moves owned retry focus to the existing heading before the %s request", (kind) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const request = vi.fn(() => { expect(screen.getByRole("heading", { name: "Passage-level matches" })).toHaveFocus(); });
    render(<ReuseExplorePanel {...props(kind === "optional" ? { excludedMatchesQuery: query({ isError: true, isSuccess: false, refetch: request }) } : { flagsError: true, flagsSucceeded: false, onRetryFlags: request })} />);
    const retry = screen.getByRole("button", { name: kind === "optional" ? "Try again" : "Retry loading recorded matches" });
    retry.focus(); fireEvent.click(retry);
    expect(request).toHaveBeenCalledOnce();
  });

  it.each(["outside", "window"])("preserves %s focus when an unowned retry is invoked", (intent) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(intent !== "window");
    const request = vi.fn();
    render(<><button>Other action</button><ReuseExplorePanel {...props({ excludedMatchesQuery: query({ isError: true, isSuccess: false, refetch: request }) })} /></>);
    const retry = screen.getByRole("button", { name: "Try again" });
    const target = intent === "outside" ? screen.getByRole("button", { name: "Other action" }) : retry;
    target.focus(); fireEvent.click(retry);
    expect(target).toHaveFocus(); expect(request).toHaveBeenCalledOnce();
  });

  it("uses actual query states through cached success, failed refetch, held retry and a late result", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let resolveRetry!: (response: Response) => void;
    let requests = 0;
    vi.stubGlobal("fetch", vi.fn(() => {
      requests++;
      if (requests === 1) return Promise.resolve(new Response(JSON.stringify({ passage_archive_size_n: 4, matches: [] }), { status: 200 }));
      if (requests === 2) return Promise.resolve(new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled unavailable comparison" } }), { status: 503 }));
      expect(screen.getByRole("heading", { name: "Passage-level matches" })).toHaveFocus();
      return new Promise<Response>((resolve) => { resolveRetry = resolve; });
    }));
    function Harness() {
      const result = useExcludedReuseMatches(5, { includeReferenceList: true, includeBlockQuote: false });
      return <><button onClick={() => result.refetch()}>Refresh comparison</button><button>Later choice</button><ReuseExplorePanel {...props({ includeReferenceList: true, excludedMatchesQuery: result })} /></>;
    }
    renderWithProviders(<Harness />);
    expect(await screen.findByText(/Comparison coverage: 4 stored comparison passages/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh comparison" }));
    const retry = await screen.findByRole("button", { name: "Try again" });
    expect(screen.queryByText(/Comparison coverage: 4/)).not.toBeInTheDocument();
    retry.focus(); fireEvent.click(retry);
    await waitFor(() => expect(requests).toBe(3));
    expect(screen.getByText("Loading comparison coverage and additional matches.")).toBeVisible();
    expect(screen.queryByText(/No additional matches found/)).not.toBeInTheDocument();
    const later = screen.getByRole("button", { name: "Later choice" }); later.focus();
    await act(async () => { resolveRetry(new Response(JSON.stringify({ passage_archive_size_n: 1, matches: [] }), { status: 200 })); });
    expect(await screen.findByText(/Comparison coverage: 1 stored comparison passage\./)).toBeVisible();
    expect(later).toHaveFocus();
  });

  it("keeps direct viewer recorded-query pending, failure and successful-empty states independent of comparison coverage", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let failFlags!: (response: Response) => void;
    let flagRequests = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(input.toString(), "http://localhost").pathname;
      if (path === "/check-runs/5/flags") {
        flagRequests++;
        if (flagRequests === 1) return new Promise<Response>((resolve) => { failFlags = resolve; });
        expect(screen.getByRole("heading", { name: "Passage-level matches" })).toHaveFocus();
        return Promise.resolve(new Response("[]", { status: 200 }));
      }
      if (path === "/check-runs/5/document/reuse-matches") return Promise.resolve(new Response(JSON.stringify({ passage_archive_size_n: 4, matches: [] }), { status: 200 }));
      if (path === "/check-runs/5/document") return Promise.resolve(new Response(JSON.stringify({ manuscript_id: 10, original_filename: "source.docx", source_format: "docx", available: false, unavailable_reason: "Controlled source unavailable", purged_at: null, page_count: null, regions: [] }), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify({ error: { code: "not_found", message: "Controlled absent report" } }), { status: 404 }));
    }));
    renderWithProviders(<SignalDocumentViewerPage />, { route: "/report/5/document?view=matches&pane=review", path: "/report/:checkRunId/document" });
    expect(await screen.findByText(/Comparison coverage: 4 stored comparison passages/)).toBeVisible();
    expect(screen.getByText("Loading recorded passage matches.")).toBeVisible();
    expect(screen.queryByText("No passage-level match was recorded for this check.")).not.toBeInTheDocument();
    await act(async () => { failFlags(new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled flags unavailable" } }), { status: 503 })); });
    const retry = await screen.findByRole("button", { name: "Retry loading recorded matches" });
    expect(screen.getByText(/Comparison coverage: 4 stored comparison passages/)).toBeVisible();
    expect(screen.queryByText("No passage-level match was recorded for this check.")).not.toBeInTheDocument();
    retry.focus(); fireEvent.click(retry);
    expect(await screen.findByText("No passage-level match was recorded for this check.")).toBeVisible();
    expect(flagRequests).toBe(2);
  });
});
