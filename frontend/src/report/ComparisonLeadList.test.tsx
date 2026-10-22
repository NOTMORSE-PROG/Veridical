import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { comparisonLeadCollection } from "./comparisonLeadCollection";
import { ComparisonLeadList } from "./ComparisonLeadList";
import { renderWithProviders } from "../test/renderWithProviders";

const RECORDS: FlagSummaryOut[] = Array.from({ length: 31 }, (_, i) => ({ id: i + 39,
  check_kind: "originality_reuse", affects_readiness: false, severity: "high", criterion_text: null,
  evidence_excerpt: "Recorded excerpt", page_anchor: "paragraph 2", overridden: i === 2,
  is_passage_level: true, first_upload_context: false, confirmed_citation_source: false,
  problem_kind: "reuse_high_similarity_passage", matched_ref: 13,
}));
afterEach(() => vi.restoreAllMocks());
describe("optional comparison list", () => {
  it("keeps requiredness, record identity, viewed and resolved quantities distinct", () => {
    const select = vi.fn();
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<ComparisonLeadList collection={collection} showActions viewedIds={new Set([39, 40])} onSelect={select} />);
    expect(screen.getByText("31 recorded comparison leads")).toBeVisible();
    expect(screen.getByText(/Marked viewed: 2 of 31 comparison leads. Resolved: 1 of 31/)).toBeVisible();
    expect(screen.queryByText(/High severity|36 tasks/)).not.toBeInTheDocument();
    const filters = within(screen.getByRole("group", { name: "Filter optional comparison leads" }));
    expect(filters.getByRole("button", { name: "All31" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(filters.getByRole("button", { name: "Resolved1" }));
    expect(screen.getByText("Comparison lead 3 of 31. Record #41.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Review evidence for Comparison lead 3 of 31. Record #41." }));
    expect(select).toHaveBeenCalledWith(41);
  });
  it("shows configured batches and focuses only its explicit newly exposed continuation", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<ComparisonLeadList collection={collection} showActions />);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    const more = screen.getByRole("button", { name: "Show 8 more comparison leads" }); more.focus(); fireEvent.click(more);
    expect(screen.getAllByRole("link")).toHaveLength(16);
    expect(screen.getByRole("link", { name: "Review evidence for Comparison lead 9 of 31. Record #47." })).toHaveFocus();
    const next = screen.getByRole("button", { name: "Show 8 more comparison leads" }); next.focus(); fireEvent.click(next);
    const last = screen.getByRole("button", { name: "Show 7 more comparison leads" }); last.focus(); fireEvent.click(last);
    expect(screen.getAllByRole("link")).toHaveLength(31);
    expect(screen.queryByRole("button", { name: /more comparison leads/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review evidence for Comparison lead 25 of 31. Record #63." })).toHaveFocus();
  });
  it.each(["fetching", "paused", "error"] as const)("keeps nonempty %s snapshot nodes with explicit last-loaded quantities", (state) => {
    const query = { data: RECORDS, fetchStatus: state === "error" ? "idle" as const : state, isError: true, isSuccess: false };
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, query)} showActions onRetry={vi.fn()} />);
    expect(screen.getByText("Last loaded: 31 recorded comparison leads")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Optional comparison leads" }).closest("section")).toHaveAttribute("aria-busy", String(state === "fetching"));
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.queryByText("No optional comparison leads are available in this view.")).not.toBeInTheDocument();
  });
  it.each([undefined, []])("does not convert absent/cached-empty error into a completed-empty claim (%j)", (data) => {
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, { data, fetchStatus: "idle", isError: true, isSuccess: false })} showActions={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Comparison lead records could not be loaded.");
    expect(screen.queryByText(/0 recorded|No optional comparison leads are available/)).not.toBeInTheDocument();
  });
  it("omits private viewed progress and actions in shared rendering", () => {
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<ComparisonLeadList collection={collection} showActions={false} />);
    expect(screen.queryByText(/Marked viewed:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Review evidence/ })).not.toBeInTheDocument();
  });
});
