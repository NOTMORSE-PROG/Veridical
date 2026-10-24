import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router";
import { OriginalityScope } from "./OriginalityScope";

describe("run-saved passage scope", () => {
  it("keeps partial and unknown qualifications beside a positive saved corpus count", () => {
    render(<MemoryRouter><OriginalityScope scope={{ compared_count: 2, corpus: "library", library_passage_indexing: {
      version: "v1", partial: true, unavailable: false, not_recorded: true,
    } }} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Passage comparison limits" })).toBeInTheDocument();
    expect(screen.getByText(/compared against 2 Library records/)).toBeInTheDocument();
    expect(screen.getByText(/Other extracted passages were not available/)).toBeInTheDocument();
    expect(screen.getByText(/Do not assume that every extracted passage was searchable/)).toBeInTheDocument();
    expect(screen.getByText(/missing match does not establish originality/)).toBeInTheDocument();
  });

  it("does not attach passage scope when no comparison could run", () => {
    render(<MemoryRouter><OriginalityScope scope={{ compared_count: 0, corpus: "library" }} /></MemoryRouter>);
    expect(screen.getByText(/No Library records were available/)).toBeInTheDocument();
    expect(screen.queryByText(/Passage coverage for this comparison/)).not.toBeInTheDocument();
  });

  it("does not reconstruct missing historical scope", () => {
    render(<MemoryRouter><OriginalityScope scope={{ compared_count: 1, corpus: "library" }} /></MemoryRouter>);
    expect(screen.getByText("Passage coverage for this comparison was not recorded.")).toBeInTheDocument();
  });
});
