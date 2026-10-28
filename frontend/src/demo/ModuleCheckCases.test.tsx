import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderWithProviders } from "../test/renderWithProviders";
import cases from "./moduleCases.json";
import { ModuleCheckCasePage } from "./ModuleCheckCases";

vi.mock("../document/PdfPane", () => ({ PdfPane: ({ fileUrl, requestedPage, selectedFlagId }: { fileUrl: string; requestedPage: number; selectedFlagId: number }) => <div data-testid="case-reader" data-file={fileUrl} data-page={requestedPage} data-selection={selectedFlagId} /> }));

describe("published module check records", () => {
  afterEach(() => window.history.replaceState({}, "", "/"));
  function render(kind: string, sample = true) {
    window.history.replaceState({}, "", sample ? `/demo/module-checks/${kind}` : `/module-checks/${kind}`);
    return renderWithProviders(<ModuleCheckCasePage />, { route: `/module-checks/${kind}`, path: "/module-checks/:kind" });
  }
  it("keeps all three original licensed PDFs and source images bound to their recorded hashes", () => {
    for (const document of cases.documents) {
      expect(document.licence_short).toBe("CC BY 4.0");
      expect(createHash("sha256").update(readFileSync(resolve(`public${document.pdf_asset}`))).digest("hex")).toBe(document.source_sha256);
    }
    for (const source of [...cases.statistics.checked_values.map(item => item.source), ...cases.reuse.pairs.flatMap(pair => pair.source_excerpt_cards.map(item => item.source))]) expect(createHash("sha256").update(readFileSync(resolve(`public${source.image_asset}`))).digest("hex")).toBe(source.image_sha256);
  });
  it("shows the actual three consistent comparisons and changes only the selected source location", () => {
    render("statistics");
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText("0.00062803")).toBeInTheDocument();
    expect(screen.getByText("0.00591177")).toBeInTheDocument();
    expect(screen.getByText("0.02300669")).toBeInTheDocument();
    expect(screen.getByTestId("case-reader")).toHaveAttribute("data-page", "9");
    fireEvent.click(screen.getByRole("button", { name: "Read PDF page 11, result 3" }));
    expect(screen.getByTestId("case-reader")).toHaveAttribute("data-page", "11");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("compares two distinct original papers and keeps their relationship visible", () => {
    render("reuse");
    const readers = screen.getAllByTestId("case-reader");
    expect(readers).toHaveLength(2);
    expect(readers[0].getAttribute("data-file")).not.toBe(readers[1].getAttribute("data-file"));
    expect(readers[0]).toHaveAttribute("data-page", "3");
    expect(readers[1]).toHaveAttribute("data-page", "4");
    expect(screen.getByText(cases.reuse.relationship.explanation)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: cases.reuse.pairs[1].title }));
    expect(screen.getByRole("heading", { name: cases.reuse.pairs[1].title })).toBeInTheDocument();
    expect(readers[0]).toHaveAttribute("data-page", "4");
    expect(readers[1]).toHaveAttribute("data-page", "4");
    expect(screen.getByText(cases.reuse.pairs[1].explanation)).toBeInTheDocument();
  });
  it("does not expose a prepared record as a real account check route", () => {
    render("statistics", false);
    expect(screen.getByText("This check record is unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("case-reader")).not.toBeInTheDocument();
  });
});
