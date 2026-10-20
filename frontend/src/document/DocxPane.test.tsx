import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
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

  it("places native tables between their original body neighbors and focuses the table location", async () => {
    const { container } = render(<DocxPane {...props}
      tables={[{ paragraph: 1, rows: [["Abstract", "Original abstract text"], ["Count", "12"]] }]}
      requestedParagraph={1} />);
    expect(screen.getByRole("table")).toHaveTextContent("Original abstract text");
    expect(screen.queryByRole("columnheader")).not.toBeInTheDocument();
    const locations = Array.from(container.querySelectorAll("[data-source-paragraph]"));
    expect(locations.map((element) => element.getAttribute("data-source-paragraph"))).toEqual(["0", "1", "2"]);
    await waitFor(() => expect(locations[1]).toHaveFocus());
  });

  it("retains image-only source positions with honest unavailable notices", async () => {
    const { container } = render(<DocxPane {...props}
      images={[{ image_index: 0, paragraph: 1, available: false, source_alt_text: null,
        width_px: null, height_px: null, unavailable_reason: "unsupported_drawing" }]}
      requestedParagraph={1} />);
    expect(screen.getByText(/This drawing cannot be displayed/)).toBeInTheDocument();
    expect(screen.getByText(/Image contents are not assessed/)).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector('[data-source-paragraph="1"]')).toHaveFocus());
    expect(screen.getAllByText("Source paragraph")).toHaveLength(1);
  });

  it("loads a source image through authenticated API and expands without rechecking", async () => {
    const load = vi.spyOn(api, "getBlob").mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:docx-test");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const { unmount } = render(<DocxPane {...props}
      imageBasePath="/check-runs/12/document/images"
      images={[{ image_index: 0, paragraph: 1, available: true, source_alt_text: "Source image",
        width_px: 8, height_px: 8, unavailable_reason: null }]} />);
    const expand = await screen.findByRole("button", { name: "Expand image 1" });
    expect(load).toHaveBeenCalledWith("/check-runs/12/document/images/0");
    fireEvent.click(expand);
    const dialog = screen.getByRole("dialog", { name: "Source image 1" });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Actual size" }));
    expect(screen.getByRole("button", { name: "Actual size" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:docx-test");
    load.mockRestore();
    create.mockRestore();
    revoke.mockRestore();
  });

  it("does not request image bytes while the source pane is hidden", async () => {
    const load = vi.spyOn(api, "getBlob");
    render(<DocxPane {...props} isVisible={false}
      imageBasePath="/check-runs/12/document/images"
      images={[{ image_index: 0, paragraph: 1, available: true, source_alt_text: null,
        width_px: 8, height_px: 8, unavailable_reason: null }]} />);
    expect(screen.getByText("Image will load when visible.")).toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
    load.mockRestore();
  });

  it("uses actual visibility when Review is selected but both desktop panels are visible", async () => {
    let intersect: IntersectionObserverCallback | undefined;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) { intersect = callback; }
      observe() {}
      disconnect() {}
    });
    const load = vi.spyOn(api, "getBlob").mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:both-panels");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    try {
      const { unmount } = render(<DocxPane {...props} isVisible={false}
        imageBasePath="/check-runs/12/document/images"
        images={[{ image_index: 0, paragraph: 1, available: true, source_alt_text: null,
          width_px: 8, height_px: 8, unavailable_reason: null }]} />);
      expect(load).not.toHaveBeenCalled();
      intersect!([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver);
      expect(load).not.toHaveBeenCalled();
      intersect!([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
      expect(await screen.findByRole("button", { name: "Expand image 1" })).toBeInTheDocument();
      expect(load).toHaveBeenCalledTimes(1);
      unmount();
    } finally {
      vi.unstubAllGlobals();
      load.mockRestore();
      create.mockRestore();
      revoke.mockRestore();
    }
  });

  it("preserves surrounding source after an image failure and retries only on request", async () => {
    const load = vi.spyOn(api, "getBlob").mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValueOnce(new Blob(["png"], { type: "image/png" }));
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:retry");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const { unmount } = render(<DocxPane {...props}
      imageBasePath="/check-runs/12/document/images"
      images={[{ image_index: 0, paragraph: 1, available: true, source_alt_text: "<script>untrusted</script>",
        width_px: 8, height_px: 8, unavailable_reason: null }]} />);
    const retry = await screen.findByRole("button", { name: "Retry image 1" });
    expect(screen.getByText("Source paragraph")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
    fireEvent.click(retry);
    expect(await screen.findByRole("button", { name: "Expand image 1" })).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("alt", "<script>untrusted</script>");
    expect(document.querySelector("script")).toBeNull();
    expect(load).toHaveBeenCalledTimes(2);
    unmount();
    load.mockRestore();
    create.mockRestore();
    revoke.mockRestore();
  });
});
