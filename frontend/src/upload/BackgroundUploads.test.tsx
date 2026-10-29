import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithUploadProviders } from "../test/renderWithUploadProviders";
import { BackgroundUploadsProvider, useBackgroundUploads } from "./BackgroundUploads";

const SUMMARY = {
  manuscript_id: 42, group_label: "Ungrouped", ingest_status: "done", page_count: 312, anchor_kind: "page",
  image_only: false, text_chars: 5000, images: 0, tables: 0, equations: 0, citations: 5, vision_status: "none",
  notes: [], group_proposal: { title: null, short_name: null, members: [], program: null, adviser: null, extraction_failed: false },
  existing_upload: null,
};

function Starter() {
  const background = useBackgroundUploads();
  return <input type="file" aria-label="Manuscript" onChange={event => { const file = event.currentTarget.files?.[0]; if (file) background?.start(file); }} />;
}

function choose(name = "large-thesis.pdf") {
  const file = new File(["%PDF"], name, { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Manuscript"), { target: { files: [file] } });
}

describe("background uploads", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the upload in a tray and offers to start a check when it is ready", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 200 })));
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    choose();
    const tray = await screen.findByRole("complementary", { name: "Uploads in progress" });
    expect(tray).toHaveTextContent("large-thesis.pdf");
    expect(await screen.findByText("Ready.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a check" })).toHaveAttribute("href", "/dashboard?check=42");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss large-thesis.pdf" }));
    expect(screen.queryByRole("complementary", { name: "Uploads in progress" })).not.toBeInTheDocument();
  });

  it("reports a failed upload honestly and lets the instructor try again", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async () => fail
      ? new Response(JSON.stringify({ error: { code: "unavailable", message: "Upload service unavailable." } }), { status: 503 })
      : new Response(JSON.stringify(SUMMARY), { status: 200 })));
    await renderWithUploadProviders(<BackgroundUploadsProvider><Starter /></BackgroundUploadsProvider>);
    choose();
    expect(await screen.findByText("Upload service unavailable.")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Ready.")).toBeInTheDocument();
  });
});
