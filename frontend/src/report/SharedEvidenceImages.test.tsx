import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createHash, webcrypto } from "node:crypto";
import type { PublicEvidenceImageOut } from "../api/types";
import { SharedEvidenceImage, SharedEvidencePager } from "./SharedEvidenceImages";

const bytes = new TextEncoder().encode("Known image fixture bytes");
const evidence: PublicEvidenceImageOut = { evidence_id: "evidence-1", image_id: `e-${"a".repeat(24)}`, image_sha256: createHash("sha256").update(bytes).digest("hex"), quote: "A saved statement", anchor: "page 5", page: 5, label: "Abstract", precision: "page_bbox", precision_note: "Exact passage" };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function start() {
  vi.stubGlobal("crypto", webcrypto);
  const create = vi.fn().mockReturnValueOnce("blob:source-a").mockReturnValueOnce("blob:source-b");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  const revoke = vi.fn(); Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
  const fetch = vi.fn<typeof window.fetch>(async () => new Response(bytes, { headers: { "Content-Type": "image/png" } })); vi.stubGlobal("fetch", fetch);
  return { create, revoke, fetch };
}
describe("shared source image controls", () => {
  it("closes an enlarged image on token change, disposes the old URL and never opens the next image automatically", async () => {
    const { revoke, fetch } = start();
    const view = render(<SharedEvidenceImage token="first" evidence={evidence} />);
    fireEvent.click(await screen.findByRole("link", { name: "View larger source image" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    view.rerender(<SharedEvidenceImage token="second" evidence={evidence} />);
    await screen.findByRole("link", { name: "View larger source image" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(revoke).toHaveBeenCalledWith("blob:source-a");
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: "omit" });
    view.unmount(); expect(revoke).toHaveBeenCalledWith("blob:source-b");
  });
  it("changes only the inline evidence on Next, preserving explicit-link-only enlargement", async () => {
    start(); render(<SharedEvidencePager token="one" images={[evidence, { ...evidence, evidence_id: "evidence-2", anchor: "page 29", page: 29, quote: "Another statement" }]} />);
    await screen.findByRole("link", { name: "View larger source image" });
    fireEvent.click(screen.getByRole("button", { name: "Next evidence" }));
    expect(screen.getByText("Evidence 2 of 2")).toBeInTheDocument();
    expect(screen.getByText(/Another statement/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("retains text and refuses unverified bytes", async () => {
    const { create } = start(); render(<SharedEvidenceImage token="one" evidence={{ ...evidence, image_sha256: "0".repeat(64) }} />);
    await waitFor(() => expect(screen.getByText(/This source image is unavailable/)).toBeInTheDocument());
    expect(screen.getByText(/A saved statement/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View larger source image" })).not.toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });
  it("shows both actual saved pages inline without opening a dialog", async () => {
    start(); render(<SharedEvidencePager token="one" images={[evidence, { ...evidence, evidence_id: "second", quote: "Second saved passage", page: 29 }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Show both source pages" }));
    expect(screen.getByText(/Second saved passage/)).toBeInTheDocument();
    expect(screen.getByText(/A saved statement/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show one page at a time" }));
    expect(screen.getByText("Evidence 1 of 2")).toBeInTheDocument();
  });
});
