// V-071 AC8/AC9: duplicate-flag collapse and problem-stating flag cards.
// FlagsList takes an already-fetched flags array directly (same seam
// Report.test.tsx/AdviserView.test.tsx already use), so these tests skip
// network stubbing entirely and drive the presentational component.
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { FlagsList } from "./FlagsPanel";

function flag(overrides: Partial<FlagSummaryOut> & { id: number }): FlagSummaryOut {
  return {
    check_kind: "statistical_forensics",
    severity: "med",
    criterion_text: null,
    evidence_excerpt: "n=5, M=4.20, SD=0.37 (Instructors)",
    page_anchor: "page 24",
    overridden: false,
    is_passage_level: false,
    first_upload_context: false,
    confirmed_citation_source: false,
    problem_kind: "grimmer_inconsistent",
    ...overrides,
  };
}

describe("FlagsList — V-071 AC8 duplicate-flag collapse", () => {
  it("collapses flags sharing check_kind, excerpt, and problem_kind into one cluster with an N-locations disclosure", async () => {
    const flags = [
      flag({ id: 1, page_anchor: "page 24" }),
      flag({ id: 2, page_anchor: "page 31" }),
      flag({ id: 3, page_anchor: "page 39" }),
    ];
    renderWithProviders(<FlagsList flags={flags} />);

    // The identical sentence renders exactly once, not three times.
    expect(screen.getAllByText("n=5, M=4.20, SD=0.37 (Instructors)")).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "3 locations" })).toBeInTheDocument();
    // Closed by default — no individual anchor pill visible yet.
    expect(screen.queryByText("page 24")).not.toBeInTheDocument();
  });

  it("expanding the disclosure reveals each duplicate's own anchor and detail link", async () => {
    const flags = [flag({ id: 1, page_anchor: "page 24" }), flag({ id: 2, page_anchor: "page 31" })];
    renderWithProviders(<FlagsList flags={flags} />);

    fireEvent.click(await screen.findByRole("button", { name: "2 locations" }));

    expect(screen.getByText("page 24")).toBeInTheDocument();
    expect(screen.getByText("page 31")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Review evidence" });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/flags/1", "/flags/2"]);
  });

  it("does NOT collapse flags with the same excerpt but a different problem_kind", async () => {
    const flags = [
      flag({ id: 1, evidence_excerpt: "Same words, different problem." }),
      flag({ id: 2, evidence_excerpt: "Same words, different problem.", problem_kind: "grim_inconsistent" }),
    ];
    renderWithProviders(<FlagsList flags={flags} />);

    expect(screen.queryByRole("button", { name: /locations/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Same words, different problem.")).toHaveLength(2);
  });

  it("shows a per-cluster resolution count when duplicates are partially resolved", async () => {
    const flags = [
      flag({ id: 1, overridden: true }),
      flag({ id: 2, overridden: false }),
      flag({ id: 3, overridden: false }),
    ];
    renderWithProviders(<FlagsList flags={flags} />);

    expect(await screen.findByText("1 of 3 resolved")).toBeInTheDocument();
  });

  it("V-071 AC8 (ux-critic finding): a cluster's open/closed URL state survives the backend's own resolved-flags-last reordering", async () => {
    // `ux-critic` reproduced live that keying the open-cluster URL param on
    // "the cluster's first flag in the CURRENT array order" broke the
    // instant one member was resolved -- the backend moves a resolved flag
    // to the end of its group (test_report_flags_live.py's own
    // unresolved-before-overridden ordering), which changes which flag is
    // first and orphans the URL param the instructor left the page with,
    // silently re-collapsing a cluster they had just acted on. Simulating
    // the POST-resolution order here (the once-first flag, id 1, now sits
    // last) with the URL param still naming "1" (what it was set to before
    // the resolution) proves the fix: the key must be the min member id,
    // not array position.
    const flags = [flag({ id: 2, overridden: true }), flag({ id: 1, overridden: false })];
    renderWithProviders(<FlagsList flags={flags} />, { route: "/?flags_clusters_open=1" });

    // If the cluster were still keyed on array-order-first (id 2 here),
    // the param "1" would match nothing and the list would stay collapsed.
    expect(await screen.findByRole("button", { name: "2 locations", expanded: true })).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toContain("/flags/1");
  });

  it("shows 'All N resolved' when every duplicate in the cluster is resolved", async () => {
    // All-resolved groups default to collapsed (groupCaption/BUG-078
    // convention) -- open the group itself before the cluster's own
    // header (unlike its inner N-locations list, not gated separately)
    // becomes visible.
    const flags = [flag({ id: 1, overridden: true }), flag({ id: 2, overridden: true })];
    renderWithProviders(<FlagsList flags={flags} />);

    fireEvent.click(await screen.findByRole("button", { name: /Statistical forensics/ }));

    expect(await screen.findByText("All 2 resolved")).toBeInTheDocument();
  });
});

describe("FlagsList — V-071 AC9 problem-stating flag cards", () => {
  it("states a short problem label for a mapped problem_kind", async () => {
    const flags = [
      flag({
        id: 1,
        check_kind: "citation_integrity",
        evidence_excerpt: "Santos, J. R. (2025). A title. Journal.",
        problem_kind: "unverifiable_not_found",
      }),
    ];
    renderWithProviders(<FlagsList flags={flags} />);

    expect(await screen.findByText("Source not found in the databases checked")).toBeInTheDocument();
  });

  it("renders no problem label for an unmapped or absent problem_kind, never a guessed one", async () => {
    const flags = [flag({ id: 1, check_kind: "internal_agreement", problem_kind: "agreement_contradictory" })];
    renderWithProviders(<FlagsList flags={flags} />);

    const excerpt = await screen.findByText(flags[0].evidence_excerpt);
    const row = excerpt.closest("div")!;
    // Only the eyebrow ("Internal agreement") and the excerpt itself --
    // no third <p> guessing a label for a problem_kind this project
    // hasn't given honest short copy to yet.
    expect(row.querySelectorAll("p")).toHaveLength(2);
  });
});
