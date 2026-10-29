// V-077 (D-028): the report says what the originality check compared
// against, so an empty Library can never read as a clean result.
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { OriginalityScope } from "./OriginalityScope";
import { originalityUnassessed } from "./originalityScopeState";

describe("OriginalityScope", () => {
  it("states an empty Library as nothing to compare against, never as originality", () => {
    renderWithProviders(<OriginalityScope scope={{ compared_count: 0, corpus: "library" }} showAddAction />);
    expect(screen.getByText("Originality had nothing to compare against")).toBeInTheDocument();
    expect(screen.getByText(/This is not evidence of originality/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Add to Library/ })).toHaveAttribute("href", "/library/add");
  });

  it("names the Library and its limit when it compared against records", () => {
    renderWithProviders(<OriginalityScope scope={{ compared_count: 1, corpus: "library" }} />);
    expect(
      screen.getByText(/compared against 1 Library record when this check ran\. Manuscripts that are not in the Library were not compared\./),
    ).toBeInTheDocument();
  });

  it("labels a check from before the Library became separate", () => {
    renderWithProviders(<OriginalityScope scope={{ compared_count: 4, corpus: "pre_library" }} />);
    expect(screen.getByText(/before the Library became a separate collection/)).toBeInTheDocument();
  });

  it("says when the count was not recorded, and renders nothing without an originality result", () => {
    const { unmount } = renderWithProviders(<OriginalityScope scope={{ compared_count: null, corpus: "library" }} />);
    expect(screen.getByText(/was not recorded/)).toBeInTheDocument();
    unmount();
    const { container } = renderWithProviders(<OriginalityScope scope={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says no comparison ran when the manuscript had no readable text, never 'the Library was empty'", () => {
    renderWithProviders(
      <OriginalityScope scope={{ compared_count: null, corpus: "library", not_compared_reason: "no_text" }} showAddAction />,
    );
    expect(screen.getByText(/no eligible text was available for this comparison/)).toBeInTheDocument();
    expect(screen.queryByText(/nothing to compare against/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Add to Library/ })).not.toBeInTheDocument();
    expect(originalityUnassessed({ compared_count: null, corpus: "library", not_compared_reason: "no_text" })).toBe(true);
  });

  it("never advises re-running a check over an empty Library", () => {
    renderWithProviders(<OriginalityScope scope={{ compared_count: 0, corpus: "library" }} />);
    expect(screen.queryByText(/run the check again/)).not.toBeInTheDocument();
    expect(screen.getByText(/No Library records were available to compare against/)).toBeInTheDocument();
  });

  it("treats zero or unrecorded as unassessed, so 'no signals' is not styled as success", () => {
    expect(originalityUnassessed({ compared_count: 0, corpus: "library" })).toBe(true);
    expect(originalityUnassessed({ compared_count: null, corpus: "library" })).toBe(true);
    expect(originalityUnassessed({ compared_count: 3, corpus: "library" })).toBe(false);
    expect(originalityUnassessed(null)).toBe(false);
  });
});

it("says no text was compared when the body could not be told apart from the reference list", () => {
  renderWithProviders(<OriginalityScope scope={{ not_compared_reason: "reference_boundary_unclear" } as never} />);
  expect(screen.getByText(/the body could not be told apart from the reference list, so no text was compared\. This is not evidence of originality\./)).toBeInTheDocument();
});
