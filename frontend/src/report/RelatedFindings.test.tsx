import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CriterionResultOut, FlagSummaryOut } from "../api/types";
import { anchorLocationKey } from "../domain/sourceAnchor";
import { readinessExclusionLine, uncountedHighLine } from "../domain/readinessExclusion";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { relatedFindings } from "../domain/relatedFindings";
import { SignalCriteriaResults } from "./SignalReviewSections";

function flag(overrides: Partial<FlagSummaryOut> = {}): FlagSummaryOut {
  return { id: 1, check_kind: "citation_integrity", severity: "med", criterion_text: null, evidence_excerpt: "Serquiña, 2025",
    page_anchor: "p. 45", overridden: false, is_passage_level: false, first_upload_context: false,
    confirmed_citation_source: false, problem_kind: "orphan_citation", ...overrides };
}

function graded(overrides: Partial<CriterionResultOut> = {}): CriterionResultOut {
  return { criterion_id: 7, text: "Quality of References", type: "semantic", kind: "semantic", weight: 1, weight_importance: "med",
    outcome: "passed", score: 100, basis: null, anchor: null, reasoning: "Recorded.", reason: null, resolution: null,
    evidence: [{ quote: "Serquiña (2025) found", anchor: "page 45" }], decision_basis: null, agreeing_passes: 3, grading_passes: 3,
    level: { name: "Exemplary", ordinal: 4, points: 4, max_points: 4 }, ...overrides };
}

describe("grades beside findings on the same pages", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads grading and finding anchors as the same location", () => {
    expect(anchorLocationKey("page 45")).toBe(anchorLocationKey("p. 45"));
    expect(anchorLocationKey("p.45")).toBe("page:45");
    expect(anchorLocationKey("paragraph 12")).toBe(anchorLocationKey("\u00b612"));
    expect(anchorLocationKey("document")).toBeNull();
  });

  it("relates only undismissed findings on the pages the grade quoted, not comparison leads", () => {
    const flags = [flag(), flag({ id: 2, page_anchor: "p. 46" }), flag({ id: 3, overridden: true }),
      flag({ id: 4, check_kind: "originality_reuse", affects_readiness: false })];
    expect(relatedFindings([{ quote: "x", anchor: "page 45" }], flags).map((f) => f.id)).toEqual([1]);
    expect(relatedFindings([{ quote: "x", anchor: "document" }], flags)).toEqual([]);
  });

  it("says the grade did not use the findings and that readiness counts them", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/3/flags": [flag(), flag({ id: 2 })] }));
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[graded()]} />, { route: "/report/3" });
    expect(await screen.findByText("2 integrity findings are on the page this grade quoted (p. 45).")).toBeInTheDocument();
    expect(screen.getByText("This grade does not use integrity findings. The overall readiness does count them.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Inspect these findings for Quality of References" })).toHaveAttribute("href", "/report/3?section=findings");
  });

  it("does not claim readiness counts findings it leaves out", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/3/flags": [flag({ affects_readiness: false, readiness_exclusion_reason: "unassessed_evidence" })] }));
    renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[graded()]} />, { route: "/report/3/document" });
    expect(await screen.findByText("This grade does not use integrity findings. It is marked not counted for readiness.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Inspect these findings/ })).toHaveAttribute("href", "/report/3/document?view=queue&pane=review");
  });

  it("adds nothing to an instructor-recorded grade or the public view", async () => {
    const fetcher = stubFetchByPath({ "/check-runs/3/flags": [flag()] });
    vi.stubGlobal("fetch", fetcher);
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView results={[graded()]} />, { route: "/shared/x" });
    expect(screen.queryByText(/integrity finding/)).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalledWith(expect.stringContaining("/flags"), expect.anything());
  });

  it("shows the adviser how an automatic grade was chosen, and nothing for an instructor's grade", () => {
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView results={[
      graded({ decision_basis: "lower_of_two", support_caution: "One quote only." }),
      graded({ criterion_id: 8, text: "Tone", agreeing_passes: null, grading_passes: null, decision_basis: null }),
    ]} />, { route: "/shared/abc" });
    expect(screen.getByText(/the lower of the two grades was used/)).toBeInTheDocument();
    expect(screen.getByText("Evidence check not confirmed: One quote only.")).toBeInTheDocument();
    expect(screen.getAllByText(/Graded automatically/)).toHaveLength(1);
  });

  it("shows what each grading pass chose on an automatic grade, privately only", () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/3/flags": [] }));
    const votes = ["Proficient", "Proficient", "Beginner"];
    const { unmount } = renderWithProviders(<SignalCriteriaResults checkRunId={3} results={[graded({ votes, agreeing_passes: 2 }),
      graded({ criterion_id: 8, text: "Tone", votes, resolution: { type: "mark_level", reason: "Mine.", ai_majority_verdict: null } })]} />, { route: "/report/3" });
    expect(screen.getAllByText(/^The grading passes disagreed: Pass 1 Proficient, Pass 2 Proficient, Pass 3 Beginner\./)).toHaveLength(1);
    unmount();
    renderWithProviders(<SignalCriteriaResults checkRunId={3} publicView results={[graded({ votes })]} />, { route: "/shared/abc" });
    expect(screen.queryByText(/grading passes disagreed/)).not.toBeInTheDocument();
  });

  it("explains why a finding is left out of readiness", () => {
    expect(readinessExclusionLine(flag({ affects_readiness: false, readiness_exclusion_reason: "comparison_lead_not_scored" })))
      .toBe("Not counted for readiness: matching text is a lead to compare, not proof of copying, so you judge it from the passages.");
    expect(readinessExclusionLine(flag({ affects_readiness: false, readiness_exclusion_reason: "something_new" }))).toBe("Not counted for readiness.");
    expect(readinessExclusionLine(flag())).toBeNull();
    expect(uncountedHighLine([flag({ check_kind: "originality_reuse", affects_readiness: false, readiness_exclusion_reason: "comparison_lead_not_scored" })]))
      .toBe("1 high-severity comparison lead is not in this count: matching text is a lead to compare, not proof of copying, so you judge it from the passages.");
    expect(uncountedHighLine([flag({ affects_readiness: false, readiness_exclusion_reason: "something_new" })]))
      .toBe("1 high-severity finding is not in this count. Each one says why under Integrity findings.");
  });
});
