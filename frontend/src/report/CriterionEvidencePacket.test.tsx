import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { CriterionEvidencePacket } from "./CriterionEvidencePacket";
import { LEGACY_ORGANIZATION_REVIEW } from "../demo/preparedAiReview";
import { sampleReport } from "../demo/workspaceFixture";
import { criterionEvidenceGuide } from "../demo/criterionEvidenceGuide";

describe("historical source correction", () => {
  beforeEach(() => { window.history.replaceState({}, "", "/demo/report/1"); });
  afterEach(() => { window.history.replaceState({}, "", "/"); });
  it("qualifies a frozen original evidence prefix without rewriting the record or adding sources", () => {
    const row = sampleReport.results[3];
    const evidence = row.evidence.slice(0, 1);
    renderWithProviders(<CriterionEvidencePacket checkRunId={1} criterionId={4} title={row.text} reasoning={LEGACY_ORGANIZATION_REVIEW} evidence={evidence} previews={false} />, { route: "/demo/report/1" });
    expect(screen.getByText(LEGACY_ORGANIZATION_REVIEW)).toBeInTheDocument();
    expect(screen.getByText(/Source correction: page 4 of the published sample contains a logo/)).toBeInTheDocument();
    expect(screen.getAllByRole("figure")).toHaveLength(1);
    expect(evidence).toEqual(row.evidence.slice(0, 1));
  });
  it("does not attach a sample correction to unrelated source text", () => {
    renderWithProviders(<CriterionEvidencePacket checkRunId={1} criterionId={4} title="Organization and Formatting" reasoning={LEGACY_ORGANIZATION_REVIEW} evidence={[{ quote: "Different manuscript", anchor: "page 2" }]} previews={false} />, { route: "/demo/report/1" });
    expect(screen.queryByText(/Source correction:/)).not.toBeInTheDocument();
  });
  it.each([0, 1, 2, 3])("keeps every full passage accessible together in its named group for criterion %s", criterionIndex => {
    const row = sampleReport.results[criterionIndex];
    const groups = criterionEvidenceGuide(1, row.criterion_id, row.text, row.evidence)!;
    const reasoning = "Full assessment remains visible while choosing related passages.";
    renderWithProviders(<CriterionEvidencePacket checkRunId={1} criterionId={row.criterion_id} title={row.text} reasoning={reasoning} evidence={row.evidence} previews={false} />, { route: "/demo/report/1" });
    const seen: string[] = [];
    groups.forEach(group => {
      if (groups.length > 1) fireEvent.click(screen.getByRole("button", { name: `${group.title} ${group.indexes.length} ${group.indexes.length === 1 ? "passage" : "passages"}` }));
      const region = screen.getByRole("region", { name: group.title });
      expect(within(region).getByText(group.explanation)).toBeInTheDocument();
      expect(within(region).getAllByRole("figure")).toHaveLength(group.indexes.length);
      group.indexes.forEach(index => { expect(within(region).getByText(`“${row.evidence[index].quote}”`)).toBeInTheDocument(); seen.push(row.evidence[index].quote); });
      expect(screen.getByText(reasoning)).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(seen.sort()).toEqual(row.evidence.map(entry => entry.quote).sort());
  });
  it("resets selection for another record and keeps a source dialog on its requested comparison", () => {
    const row = sampleReport.results[0];
    const renderPacket = (run: number, selectedIndex?: number) => <CriterionEvidencePacket checkRunId={run} criterionId={1} title={row.text} reasoning={row.reasoning} evidence={row.evidence} previews={false} selectedIndex={selectedIndex} />;
    const view = renderWithProviders(renderPacket(1), { route: "/demo/report/1" });
    fireEvent.click(screen.getByRole("button", { name: /Plans need to be separated/ }));
    expect(screen.getByRole("region", { name: "Plans need to be separated from completed work" })).toBeInTheDocument();
    view.rerender(renderPacket(2));
    expect(screen.getAllByRole("figure")).toHaveLength(row.evidence.length);
    expect(screen.queryByRole("navigation", { name: /Evidence groups/ })).not.toBeInTheDocument();
    view.rerender(renderPacket(1, 2));
    expect(screen.getByRole("region", { name: "The database descriptions disagree" })).toBeInTheDocument();
    expect(screen.getAllByRole("figure")).toHaveLength(3);
    expect(screen.queryByRole("navigation", { name: /Evidence groups/ })).not.toBeInTheDocument();
  });
});
