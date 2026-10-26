import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { stubFetchByPath } from "../test/renderWithProviders";
import { FlagDetailPage } from "./FlagDetail";
vi.mock("../document/ManuscriptSourcePane", () => ({ OwnedManuscriptSourcePane: ({ anchor }: { anchor?: string | null }) => <div data-testid="owned-source" data-anchor={anchor ?? ""}>Owned source</div> }));

const leads: FlagSummaryOut[] = Array.from({ length: 31 }, (_, index) => ({
  id: index + 39, check_kind: "originality_reuse", affects_readiness: false,
  severity: "high", criterion_text: null, evidence_excerpt: "Saved comparison passage",
  page_anchor: "paragraph 3", overridden: false, is_passage_level: false,
  first_upload_context: false, confirmed_citation_source: false, problem_kind: "reuse_high_similarity",
}));
const flag = { ...leads[2], check_result_id: 9, check_run_id: 21, manuscript_group_label: "Test group",
  confidence: null, annotation: null, override_reason: null, ai_verdict_summary: "reuse_high_similarity",
  ai_reasoning: "Saved comparison summary", llm_mode: "real", citation_source_key: null,
};

describe("standalone comparison identity", () => {
  afterEach(() => vi.unstubAllGlobals());
  it.each([true, false])("uses only already cached complete context (cached=%s)", async (cached) => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    if (cached) queryClient.setQueryData(["report", 21, "flags"], [...leads].reverse());
    const fetch = stubFetchByPath({ "/flags/41": flag, "/auth/me": { id: 1 } });
    vi.stubGlobal("fetch", fetch);
    const router = createMemoryRouter([{ path: "/flags/:flagId", element: <FlagDetailPage /> }], {
      initialEntries: ["/flags/41"],
    });
    render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByText(cached ? "Comparison lead 3 of 31. Record #41." : "Record #41")).toBeVisible();
    expect(screen.getByText("Comparison lead: not scored")).toBeVisible();
    expect(screen.queryByText("High severity")).not.toBeInTheDocument();
    expect(screen.queryByText("Comparison lead 1 of 1. Record #41.")).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([input]) => String(input).includes("/check-runs/21/flags"))).toBe(false);
  });
});
