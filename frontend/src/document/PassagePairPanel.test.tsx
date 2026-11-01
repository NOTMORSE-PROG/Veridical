import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { PassagePairPanel } from "./PassagePairPanel";

describe("passage comparison identity claims", () => {
  it.each(["exact_duplicate", "high_similarity", null] as const)("preserves a comparison with classification %s without an identity claim", (level) => {
    renderWithProviders(<PassagePairPanel variant="flag" ownAnchor="page 1" pair={{ level, similarity: 0.972, own_excerpt: "The records service retains the original source. First document.", matched_excerpt: "The records service retains the original source. Different document.", own_context_before: null, own_context_after: null, matched_context_before: null, matched_context_after: null, matched_ref: 5, context_words_each_side: 60 }} />);
    expect(screen.getByText(/does not establish identical passages or files/)).toBeInTheDocument();
    expect(screen.getByText(/copying, authorship or which document came first/)).toBeVisible();
    expect(screen.queryByText(/Exact duplicate|High textual similarity/)).not.toBeInTheDocument();
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /^Open in Library/ })).toHaveAttribute("href", "/library/5");
  });
});

it("names standard wording that was not counted right under the shading line it qualifies", () => {
  renderWithProviders(<PassagePairPanel variant="flag" ownAnchor="page 1" standardWordingLeftOut={["ISO/IEC 25010 usability definition"]}
    pair={{ level: "high_similarity", similarity: 0.9, own_excerpt: "The records service retains the original source.", matched_excerpt: "The records service retains the original source.", own_context_before: null, own_context_after: null, matched_context_before: null, matched_context_after: null, matched_ref: 5, context_words_each_side: 60 }} />);
  const note = document.querySelector(".signal-reading-note")!;
  const legend = [...note.querySelectorAll("li")].find(item => /Shaded wording|No stable exact phrase/.test(item.textContent ?? ""))!;
  const block = note.querySelector(".signal-standard-wording-left-out")!;
  expect(block).not.toBeNull();
  expect(legend.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(note.textContent).toContain("ISO/IEC 25010 usability definition");
  expect(note.textContent).toContain("Shading shows all shared wording, including this standard wording. VERIDICAL did not count the standard wording.");
});
