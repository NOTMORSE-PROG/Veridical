import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PassageIndexingDetails } from "./PassageIndexingDetails";
import { passageAdditionAnnouncement, passageIndexingMarker } from "./passageIndexing";

describe("saved Library preparation scope", () => {
  it("explains a limited successful addition without making it a grade", () => {
    const value = { state: "partial" as const, passages_found: 11, passages_indexed: 3 };
    render(<PassageIndexingDetails value={value} />);
    expect(screen.getByRole("heading", { name: "Automatic passage matching" })).toBeInTheDocument();
    expect(screen.getByText(/3 of 11 extracted passages/)).toBeInTheDocument();
    expect(screen.getByText(/Full-document and section comparisons have separate evidence/)).toBeInTheDocument();
    expect(passageAdditionAnnouncement(value)).toBe("added with limited passage matching");
    expect(screen.queryByText(/Ready|Failed|first pages/)).not.toBeInTheDocument();
  });

  it("keeps legacy or inconsistent counts unknown", () => {
    render(<PassageIndexingDetails value={{ state: "complete", passages_found: 10, passages_indexed: 3 }} />);
    expect(screen.getByText(/saved record does not establish how many/)).toBeInTheDocument();
    expect(passageIndexingMarker(undefined)).toBe("Passage coverage not recorded");
  });

  it("does not warn on a measured full inventory and distinguishes zero", () => {
    expect(passageIndexingMarker({ state: "complete", passages_found: 3, passages_indexed: 3 })).toBeNull();
    expect(passageIndexingMarker({ state: "complete", passages_found: 0, passages_indexed: 0 })).toBe("No passages prepared");
    expect(passageIndexingMarker({ state: "unavailable", passages_found: null, passages_indexed: null })).toBe("Passage matching unavailable");
  });
});
