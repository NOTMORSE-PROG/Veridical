import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { PassagePairPanel } from "./PassagePairPanel";

describe("passage comparison identity claims", () => {
  it("labels a recorded literal span independently of the vector value", () => {
    renderWithProviders(<PassagePairPanel variant="flag" ownAnchor="page 2" pair={{
      level: "literal_overlap", similarity: 0.42, own_excerpt: "A short visible excerpt.", matched_excerpt: "A short visible excerpt.",
      own_context_before: null, own_context_after: null, matched_context_before: null, matched_context_after: null,
      matched_ref: 5, context_words_each_side: 60,
      literal_overlap: { normalization: "casefold_whitespace_tokens_v1", word_count: 45, own_start: 0, own_end: 250, matched_start: 15, matched_end: 265 },
    }} />);
    expect(screen.getByText(/Shared wording: 45 consecutive words/)).toBeVisible();
    expect(screen.getByText(/Punctuation and numbers are retained/)).toBeVisible();
    expect(screen.queryByText(/42%|High textual similarity/)).not.toBeInTheDocument();
  });
  it.each(["exact_duplicate", "high_similarity", null] as const)("preserves a comparison with classification %s without an identity claim", (level) => {
    renderWithProviders(<PassagePairPanel variant="flag" ownAnchor="page 1" pair={{ level, similarity: 0.972, own_excerpt: "The records service retains the original source. First document.", matched_excerpt: "The records service retains the original source. Different document.", own_context_before: null, own_context_after: null, matched_context_before: null, matched_context_after: null, matched_ref: 5, context_words_each_side: 60 }} />);
    expect(screen.getByText(/does not establish identical passages or files/)).toBeInTheDocument();
    expect(screen.getByText(/copying, authorship or which document came first/)).toBeVisible();
    expect(screen.queryByText(/Exact duplicate|High textual similarity/)).not.toBeInTheDocument();
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /^Open in Library/ })).toHaveAttribute("href", "/library/5");
  });
});
