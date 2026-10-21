import { describe, expect, it } from "vitest";
import { instructorAnchor } from "./sourceAnchor";
import { findingVerificationPrompt } from "./checkKind";

describe("source positions and finding-specific guidance", () => {
  it("renders stored zero-based DOCX ordinals one-based without changing links", () => {
    expect(instructorAnchor("paragraph 70")).toBe("Extracted paragraph 71");
    expect(instructorAnchor("¶0")).toBe("Extracted paragraph 1");
    expect(instructorAnchor("p. 4")).toBe("p. 4");
  });
  it("does not present an unsafe ordinal as a valid location", () => {
    expect(instructorAnchor("paragraph 99999999999999999999")).toContain("unavailable");
  });
  it("distinguishes a possibly unused reference from a missing list entry", () => {
    expect(findingVerificationPrompt("citation_integrity", "uncited_reference", false))
      .toContain("text, tables, or captions");
    expect(findingVerificationPrompt("citation_integrity", "orphan_in_text_citation", false))
      .toContain("reference list");
  });
});
