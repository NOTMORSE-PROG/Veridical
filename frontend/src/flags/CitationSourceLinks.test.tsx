import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CitationSourceLinks } from "./CitationSourceLinks";
import type { FlagOut } from "../api/types";

const flag = { check_kind: "citation_integrity", ai_verdict_summary: "uncited_reference", citation_source_locator: {
  basis: "saved_reference", order_index: 0,
  links: [{ kind: "doi_as_cited", url: "https://doi.org/10.1234%2Fexample" }, { kind: "url_as_cited", url: "https://example.org/reference" }],
} } satisfies Pick<FlagOut, "check_kind" | "ai_verdict_summary" | "citation_source_locator">;

describe("saved citation source navigation", () => {
  it("shows the saved destination with deliberate private navigation and no confirmation action", () => {
    render(<CitationSourceLinks flag={flag} />);
    expect(screen.getByText("DOI as cited")).toBeVisible();
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    for (const link of links) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(link).toHaveTextContent(link.getAttribute("href")!);
    }
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it.each(["possible_metadata_mismatch", "orphan_in_text_citation"])("does not invent links for %s without its own saved locator", (kind) => {
    render(<CitationSourceLinks flag={{ ...flag, ai_verdict_summary: kind, citation_source_locator: kind === "possible_metadata_mismatch" ? flag.citation_source_locator : null }} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it.each(["javascript:alert(1)", "https://u:p@example.org/", "https://example.org/line\nbreak", "https://example.org\\wrong"])("withholds unsafe destination %s", (url) => {
    render(<CitationSourceLinks flag={{ ...flag, citation_source_locator: { ...flag.citation_source_locator, links: [{ kind: "url_as_cited", url }] } }} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("does not interpret a locator from another check as a citation", () => {
    render(<CitationSourceLinks flag={{ ...flag, check_kind: "internal_agreement" }} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
