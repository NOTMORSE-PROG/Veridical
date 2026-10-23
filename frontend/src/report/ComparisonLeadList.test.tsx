import { act, fireEvent, screen, within } from "@testing-library/react";
import { useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { comparisonLeadCollection } from "./comparisonLeadCollection";
import { ComparisonLeadList } from "./ComparisonLeadList";
import { renderWithProviders } from "../test/renderWithProviders";
import { rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";

const RECORDS: FlagSummaryOut[] = Array.from({ length: 31 }, (_, i) => ({ id: i + 39,
  check_kind: "originality_reuse", affects_readiness: false, severity: "high", criterion_text: null,
  evidence_excerpt: "Recorded excerpt", page_anchor: "paragraph 2", overridden: i === 2,
  is_passage_level: true, first_upload_context: false, confirmed_citation_source: false,
  problem_kind: "reuse_high_similarity_passage", matched_ref: 13,
}));
afterEach(() => vi.restoreAllMocks());
describe("optional comparison list", () => {
  it.each(["row", "page", "external"])("recovers only an owned %s focus when a successful update removes the last open page", (owner) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let update!: (data: FlagSummaryOut[]) => void;
    function Harness() {
      const [data, setData] = useState(RECORDS); update = setData;
      return <><button>Other work</button><ComparisonLeadList collection={comparisonLeadCollection(5, { data, fetchStatus: "idle", isError: false, isSuccess: true })} showActions /></>;
    }
    renderWithProviders(<Harness />, { route: "/report/5?lead_view=open&lead_page=4" });
    const oldTarget = owner === "page" ? screen.getByRole("button", { name: "Page 4" }) : document.getElementById("comparison-lead-64")!;
    oldTarget.focus(); expect(oldTarget).toHaveFocus();
    if (owner === "external") screen.getByRole("button", { name: "Other work" }).focus();
    act(() => update(RECORDS.map((record) => record.id >= 64 ? { ...record, overridden: true } : record)));
    expect(oldTarget.isConnected).toBe(false);
    expect(screen.getByRole("button", { name: "Page 3" })).toHaveAttribute("aria-current", "page");
    if (owner === "external") expect(screen.getByRole("button", { name: "Other work" })).toHaveFocus();
    else expect(screen.getByRole("heading", { name: "Comparison leads" })).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(/records changed.*page 3 of 3/i);
  });
  it("retains full permitted shared evidence in uniquely identified disclosures on every page", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const data = RECORDS.map((record) => ({ ...record, evidence_excerpt: `Public excerpt ${record.id}`, first_upload_context: record.id === 69, annotation: "PRIVATE annotation", override_reason: "PRIVATE reason" }));
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, { data, fetchStatus: "idle", isError: false, isSuccess: true })} showActions={false} viewedIds={new Set([39])} />);
    expect(screen.queryByText("Viewed")).not.toBeInTheDocument();
    const first = screen.getByText("Evidence", { selector: "summary#comparison-lead-39" });
    expect(first).toHaveAccessibleName(/^Evidence for Comparison lead 1 of 31\. Record #39\./);
    expect(screen.getByText(/Public excerpt 39/)).not.toBeVisible();
    fireEvent.click(first);
    expect(screen.getByText(/Public excerpt 39/)).toBeVisible();
    const page = screen.getByRole("button", { name: "Page 4" }); page.focus(); fireEvent.click(page);
    expect(document.getElementById("comparison-lead-63")).toHaveFocus();
    fireEvent.click(screen.getByText("Evidence", { selector: "summary#comparison-lead-69" }));
    expect(screen.getByText(/Public excerpt 69/)).toBeVisible();
    expect(screen.getByText(/First-upload context:/)).toBeVisible();
    expect(document.body).not.toHaveTextContent("PRIVATE");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("preserves unrelated query, hash and return context while new actions replace legacy list growth", () => {
    function Probe() { const location = useLocation(); const navigate = useNavigate(); return <><button onClick={() => void navigate({ pathname: "/report/5", search: "?lead_visible=32&desk=kept", hash: "#integrity-signals" }, { state: { returnContext: "keep" } })}>Set return context</button><output data-testid="route">{JSON.stringify(location)}</output></>; }
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<><Probe /><ComparisonLeadList collection={collection} showActions /></>, { route: "/report/5" });
    fireEvent.click(screen.getByRole("button", { name: "Set return context" }));
    fireEvent.click(screen.getByRole("button", { name: "Page 4" }));
    const location = JSON.parse(screen.getByTestId("route").textContent!);
    expect(new URLSearchParams(location.search).get("desk")).toBe("kept");
    expect(new URLSearchParams(location.search).get("lead_visible")).toBeNull();
    expect(new URLSearchParams(location.search).get("lead_page")).toBe("4");
    expect(location.hash).toBe("#integrity-signals"); expect(location.state).toEqual({ returnContext: "keep" });
  });
  it.each([false, true])("waits beyond the old return timeout, without resurrecting focus after newer intent (%s)", async (retire) => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    let release!: () => void;
    function DelayedList() {
      const [data, setData] = useState<FlagSummaryOut[]>();
      const heading = useRef<HTMLHeadingElement>(null);
      useRouteFocus("Return control", heading, undefined, { prefix: "comparison-lead-", pending: data === undefined });
      release = () => setData([...RECORDS]);
      return <main><h1 ref={heading} tabIndex={-1}>Review report</h1><button>Later action</button><ComparisonLeadList collection={comparisonLeadCollection(5, { data, fetchStatus: data ? "idle" : "fetching", isError: false, isSuccess: Boolean(data) })} showActions /></main>;
    }
    rememberRouteReturnFocus("/report/5?lead_visible=32", "/flags/69", "comparison-lead-69", { armed: true, fallbackElementId: "comparison-leads-heading" });
    renderWithProviders(<DelayedList />, { route: "/report/5?lead_visible=32" });
    expect(screen.getByRole("heading", { name: "Review report" })).toHaveFocus();
    if (retire) screen.getByRole("button", { name: "Later action" }).focus();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 600)); });
    act(() => release());
    if (retire) {
      expect(screen.getByRole("button", { name: "Later action" })).toHaveFocus();
      expect(document.getElementById("comparison-lead-69")).not.toBeInTheDocument();
    } else {
      expect(document.getElementById("comparison-lead-69")).toHaveFocus();
      expect(screen.getAllByRole("link")).toHaveLength(7);
      act(() => release());
      expect(document.getElementById("comparison-lead-69")).toHaveFocus();
    }
  });
  it("keeps a returned resolved record outside the open filter until explicitly revealed", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true })} showActions />, { route: "/report/5?lead_view=open&lead_return=41&lead_page=4" });
    expect(screen.getByRole("button", { name: "Open" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Record #41 is outside the current filter.")).toBeVisible();
    const reveal = screen.getByRole("button", { name: "Show all comparison leads" }); reveal.focus(); fireEvent.click(reveal);
    expect(document.getElementById("comparison-lead-41")).toHaveFocus();
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });
  it("keeps optionality, identity and actual resolution distinct without repeated progress counters", () => {
    const select = vi.fn();
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<ComparisonLeadList collection={collection} showActions viewedIds={new Set([39, 40])} onSelect={select} />);
    expect(screen.getByText("31 records")).toBeVisible();
    expect(screen.queryByText(/Marked viewed:/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Viewed")).toHaveLength(2);
    expect(screen.getByText("Not scored. Does not affect readiness or block your decision.")).toBeVisible();
    expect(screen.queryByText(/High severity|36 tasks/)).not.toBeInTheDocument();
    const filters = within(screen.getByRole("group", { name: "Filter comparison leads" }));
    expect(filters.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(filters.getByRole("button", { name: "Resolved" }));
    expect(screen.getByText("L3")).toBeVisible();
    expect(screen.getByText("Record #41")).toBeVisible();
    expect(screen.getByText("1 resolved record")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /^Review evidence for Comparison lead 3 of 31\. Record #41\./ }));
    expect(select).toHaveBeenCalledWith(41);
  });
  it("uses bounded pages and focuses the first actual new record only after explicit navigation", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    const collection = comparisonLeadCollection(5, { data: RECORDS, fetchStatus: "idle", isError: false, isSuccess: true });
    renderWithProviders(<ComparisonLeadList collection={collection} showActions />);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    const next = screen.getByRole("button", { name: "Next" }); next.focus(); fireEvent.click(next);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.getByRole("link", { name: /^Review evidence for Comparison lead 9 of 31\. Record #47\./ })).toHaveFocus();
    expect(screen.queryByRole("link", { name: /Record #39\./ })).not.toBeInTheDocument();
    const last = screen.getByRole("button", { name: "Page 4" }); last.focus(); fireEvent.click(last);
    expect(screen.getAllByRole("link")).toHaveLength(7);
    expect(screen.getByRole("link", { name: /^Review evidence for Comparison lead 25 of 31\. Record #63\./ })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /more comparison leads/ })).not.toBeInTheDocument();
  });
  it.each(["fetching", "paused", "error"] as const)("keeps nonempty %s snapshot nodes with explicit last-loaded quantities", (state) => {
    const query = { data: RECORDS, fetchStatus: state === "error" ? "idle" as const : state, isError: true, isSuccess: false };
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, query)} showActions onRetry={vi.fn()} />);
    expect(screen.getByText("31 records")).toBeVisible();
    expect(screen.getByText(/Showing last loaded records/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Comparison leads" }).closest("section")).toHaveAttribute("aria-busy", String(state === "fetching"));
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.queryByText("No comparison leads were recorded for this check.")).not.toBeInTheDocument();
  });
  it.each([undefined, []])("does not convert absent/cached-empty error into a completed-empty claim (%j)", (data) => {
    renderWithProviders(<ComparisonLeadList collection={comparisonLeadCollection(5, { data, fetchStatus: "idle", isError: true, isSuccess: false })} showActions={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Comparison leads could not be loaded.");
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
