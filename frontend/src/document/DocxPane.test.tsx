import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocxPane } from "./DocxPane";

const props = {
  paragraphs: [{ paragraph: 0, text: "Source heading", heading_level: 1 }, { paragraph: 2, text: "Source paragraph", heading_level: null }],
  paragraphsPending: false, paragraphsError: false, onRetry: vi.fn(),
  regions: [], flags: [], selectedFlagId: null, onSelectFlag: vi.fn(), isVisible: true,
};

describe("recorded DOCX source navigation", () => {
  it.each([[0, "Source heading"], [2, "Source paragraph"]] as const)("focuses paragraph %s without inventing a finding", async (paragraph, text) => {
    render(<DocxPane {...props} requestedParagraph={paragraph} />);
    await waitFor(() => expect(screen.getByText(text)).toHaveFocus());
    expect(screen.queryByRole("button", { name: /Finding/ })).not.toBeInTheDocument();
  });

  it("waits for loaded paragraphs before moving to the recorded source", async () => {
    const { rerender } = render(<DocxPane {...props} paragraphs={undefined} paragraphsPending requestedParagraph={2} />);
    expect(screen.getByText(/Loading manuscript text/)).toBeInTheDocument();
    rerender(<DocxPane {...props} requestedParagraph={2} />);
    await waitFor(() => expect(screen.getByText("Source paragraph")).toHaveFocus());
  });
});
