import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { CriterionEvidencePacket } from "./CriterionEvidencePacket";
import { LEGACY_ORGANIZATION_REVIEW } from "../demo/preparedAiReview";
import { sampleReport } from "../demo/workspaceFixture";

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
});
