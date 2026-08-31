import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LibraryContentPane } from "./LibraryContentPane";

vi.mock("../document/PdfPane", () => ({ PdfPane: () => <div>PDF viewer</div> }));
vi.mock("../document/DocxPane", () => ({ DocxPane: () => <div>DOCX viewer</div> }));

describe("LibraryContentPane", () => {
  it("renders a terminal removed notice before any disabled-query pending state", () => {
    render(
      <LibraryContentPane
        manuscriptId={1}
        isOwn
        ownDocument={{
          viewer: undefined,
          isPending: true,
          isError: false,
          removedAt: "2026-08-20T08:00:00Z",
          onRetry: vi.fn(),
        }}
      />,
    );

    expect(screen.getByText("This manuscript is no longer stored in the Library.")).toBeInTheDocument();
    expect(screen.queryByText("Loading manuscript.")).not.toBeInTheDocument();
  });
});
