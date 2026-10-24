import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { NativeMathSourceOut } from "../api/types";
import { DocxPane } from "./DocxPane";

const props = {
  paragraphs: [{ paragraph: 0, text: "Independent preceding text.", heading_level: null }, { paragraph: 2, text: "Independent following text.", heading_level: null }],
  paragraphsPending: false, paragraphsError: false, onRetry: vi.fn(),
  regions: [], flags: [], selectedFlagId: null, onSelectFlag: vi.fn(), isVisible: true,
};
const native: NativeMathSourceOut = {
  state: "complete", scope: "main_document_body", occurrence_count: 1,
  containers: [{ paragraph: 1, kind: "paragraph", pieces: [
    { kind: "text", text: "p = .", occurrence: null },
    { kind: "native_math", text: null, occurrence: 0 },
    { kind: "text", text: "001", occurrence: null },
  ] }],
};

describe("native Word source gaps", () => {
  it("keeps a math-only source container at its ordinal, navigates there and separates ordinary fragments", () => {
    const place = vi.fn();
    const { container } = render(<DocxPane {...props} nativeMath={native} requestedParagraph={1} onPlaceRequestedParagraph={place} />);
    const target = container.querySelector('[data-source-paragraph="1"]');
    expect(target).not.toBeNull();
    expect(place).toHaveBeenCalledWith(target);
    expect(target?.textContent).toContain("p = . [Native Word equation not displayed] 001");
    expect(target?.textContent).not.toContain("p = .001");
    expect(screen.getByText("1 native Word equation occurrence · Main document body")).toBeVisible();
    expect(props.onSelectFlag).not.toHaveBeenCalled();
  });

  it("keeps measured zero separate from an older record's unknown count", () => {
    const { rerender } = render(<DocxPane {...props} nativeMath={{ ...native, occurrence_count: 0, containers: [] }} />);
    expect(screen.getByText("0 native Word equation occurrences · Main document body")).toBeVisible();
    expect(screen.queryByText(/Their presence and count are unknown/)).not.toBeInTheDocument();
    rerender(<DocxPane {...props} nativeMath={{ ...native, state: "not_recorded", occurrence_count: null, containers: [] }} />);
    expect(screen.getByText(/This saved extraction has no native Word equation inventory/)).toBeVisible();
    expect(screen.queryByText(/0 native Word equation occurrences/)).not.toBeInTheDocument();
  });

  it("does not describe invalid retained metadata as never saved", () => {
    render(<DocxPane {...props} nativeMath={{ ...native, state: "unavailable", occurrence_count: null, containers: [] }} />);
    expect(screen.getByText(/could not be verified against this source/)).toBeVisible();
    expect(screen.queryByText(/has no native Word equation inventory/)).not.toBeInTheDocument();
  });

  it("renders separated cell prose instead of a falsely joined numeric cell", () => {
    const { container } = render(<DocxPane {...props} nativeMath={native}
      tables={[{ paragraph: 1, rows: [[""]], native_cells: [{ display_coordinates: [[0, 0]], pieces: native.containers[0].pieces }] }]} />);
    const cell = container.querySelector("td");
    expect(cell?.textContent).toBe("p = . [Native Word equation not displayed] 001");
    expect(cell?.textContent).not.toBe("p = .001");
  });

  it("keeps merged aliases, independent cells and unplaced nested equations separate", () => {
    const { container } = render(<DocxPane {...props} nativeMath={{ ...native, occurrence_count: 2, containers: [] }}
      tables={[{ paragraph: 1, rows: [["", "", "Independent value"]], native_cells: [
        { display_coordinates: [[0, 0], [0, 1]], pieces: native.containers[0].pieces },
        { display_coordinates: [], pieces: [{ kind: "native_math", text: null, occurrence: 1 }] },
      ] }]} />);
    const cells = container.querySelectorAll("td");
    expect(cells[0].textContent).toBe("p = . [Native Word equation not displayed] 001");
    expect(cells[1].textContent).toBe(cells[0].textContent);
    expect(cells[2].textContent).toBe("Independent value");
    expect(screen.getByText(/Native Word equation in a nested table cell/)).toBeVisible();
    expect(screen.getAllByText("[Native Word equation not displayed]")).toHaveLength(3);
  });
});


describe("native omission context and real table slots", () => {
  it("shows one assessment qualification and does not add a gap label above an already represented table occurrence", () => {
    render(<DocxPane {...props} nativeMath={{ ...native, containers: [{ ...native.containers[0], kind: "table_cell" }] }}
      tables={[{ paragraph: 1, rows: [[""]], native_cells: [{ display_coordinates: [[0, 0]], pieces: native.containers[0].pieces }] }]} />);
    expect(screen.getAllByText(/formulas are not displayed in this reconstruction or evaluated by statistical forensics/)).toHaveLength(1);
    expect(screen.getAllByText("[Native Word equation not displayed]")).toHaveLength(1);
    expect(screen.queryByText("Native Word equation not displayed")).not.toBeInTheDocument();
    expect(screen.getByRole("cell")).toHaveTextContent("p = . [Native Word equation not displayed] 001");
  });
  it("retains a table occurrence when its slot is absent from the reconstructed table", () => {
    render(<DocxPane {...props} nativeMath={{ ...native, containers: [{ ...native.containers[0], kind: "table_cell" }] }}
      tables={[{ paragraph: 1, rows: [["Separate retained cell"]], native_cells: [] }]} />);
    expect(screen.getByText("[Native Word equation not displayed]")).toBeVisible();
    expect(screen.getByRole("cell")).toHaveTextContent("Separate retained cell");
    expect(screen.getByText(/p =/)).toBeVisible();
  });
});
