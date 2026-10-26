import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { evidenceFor } from "../demo/sourceEvidence";
import { SavedSourceEvidence } from "./SavedSourceEvidence";

const entries = evidenceFor("flag", 1);

describe("saved source navigation", () => {
  it("keeps source navigation available when pages are not presented", () => {
    const select = vi.fn();
    render(<SavedSourceEvidence entries={entries} onSelect={select} />);
    fireEvent.click(screen.getByRole("button", { name: "Show statement A in manuscript" }));
    expect(select).toHaveBeenCalledWith(entries.find(entry => entry.role === "statement_a"));
    expect(screen.getByRole("button", { name: "Show statement B in manuscript" })).toBeVisible();
  });

  it("hides only navigation for exact evidence already presented and restores it on leaving the pair", () => {
    const select = vi.fn();
    const a = entries.find(entry => entry.role === "statement_a")!;
    const b = entries.find(entry => entry.role === "statement_b")!;
    const { rerender } = render(<SavedSourceEvidence entries={entries} onSelect={select} presentedEvidenceIds={[a.evidence_id]} />);
    expect(screen.queryByRole("button", { name: "Show statement A in manuscript" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show statement B in manuscript" })).toBeVisible();
    rerender(<SavedSourceEvidence entries={entries} onSelect={select} presentedEvidenceIds={[a.evidence_id, b.evidence_id]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    for (const entry of entries) {
      expect(screen.getByRole("heading", { name: entry.label })).toBeVisible();
      expect(screen.getByText(`“${entry.quote}”`)).toBeVisible();
    }
    rerender(<SavedSourceEvidence entries={entries} onSelect={select} presentedEvidenceIds={[]} />);
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("keeps keyboard focus at the evidence heading when its focused action becomes redundant", () => {
    const a = entries.find(entry => entry.role === "statement_a")!;
    const { rerender } = render(<SavedSourceEvidence entries={entries} onSelect={vi.fn()} />);
    screen.getByRole("button", { name: "Show statement A in manuscript" }).focus();
    rerender(<SavedSourceEvidence entries={entries} onSelect={vi.fn()} presentedEvidenceIds={[a.evidence_id]} />);
    expect(screen.getByRole("heading", { name: a.label })).toHaveFocus();
  });
});
