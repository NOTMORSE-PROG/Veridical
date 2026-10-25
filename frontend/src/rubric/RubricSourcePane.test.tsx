import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { RubricSourcePane } from "./RubricSourcePane";

const version = { id: 5, version: 2, rubric_family_id: "family-exact" };
const hash = "a".repeat(64);
const metadata = { rubric_id: 5, version: 2, rubric_family_id: "family-exact", available: true,
  source_format: "docx", source_sha256: hash, unavailable_reason: null, reconstruction: "fresh_local_source" };
const content = { paragraphs: [{ paragraph: 7, text: "The actual uploaded requirement", heading_level: null }],
  tables: [], images: [], omitted_image_occurrences: 0,
  native_math: { state: "complete", occurrence_count: 0, containers: [] } };
const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => ({ GlobalWorkerOptions: {}, getDocument: pdf.getDocument }));

function renderSource(onUploadNewVersion = vi.fn()) {
  return renderWithProviders(<RubricSourcePane rubric={version} isVisible onUploadNewVersion={onUploadNewVersion} />, { authInstructorId: 1 });
}

describe("required-format source", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("opens the exact owned draft reading aid without assessment or Library writes", async () => {
    const fetch = stubFetchByPath({ "/rubrics/5/document": metadata,
      "/rubrics/5/document/paragraphs": { ...metadata, content } });
    vi.stubGlobal("fetch", fetch);
    renderSource();
    expect(await screen.findByText("The actual uploaded requirement")).toHaveAttribute("data-source-paragraph", "7");
    expect(screen.getByRole("region", { name: "Original format, scrollable" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Required format reading view" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Source view details" }));
    expect(screen.getByText(/not a record of the text previously sent for criteria preparation/)).toBeVisible();
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    expect(fetch.mock.calls.some(([input]) => input.toString().includes(`source_sha256=${hash}`))).toBe(true);
    expect(fetch.mock.calls.some(([input]) => /library|check-runs/.test(input.toString()))).toBe(false);
  });

  it.each([{ rubric_id: 8 }, { version: 3 }, { rubric_family_id: "other-family" }])("refuses another source identity %j", async (mismatch) => {
    const fetch = stubFetchByPath({ "/rubrics/5/document": { ...metadata, ...mismatch } });
    vi.stubGlobal("fetch", fetch);
    renderSource();
    expect(await screen.findByRole("heading", { name: "The original format could not be opened" })).toBeInTheDocument();
    expect(fetch.mock.calls).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Download original" })).not.toBeInTheDocument();
  });

  it("does not display a reading view from changed source bytes", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": metadata,
      "/rubrics/5/document/paragraphs": { ...metadata, source_sha256: "b".repeat(64), content } }));
    renderSource();
    expect(await screen.findByRole("alert")).toHaveTextContent("The format reading view could not be opened.");
    expect(screen.queryByText("The actual uploaded requirement")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download original" })).toBeInTheDocument();
  });

  it("keeps a missing local original distinct from a reader failure and offers a new version", async () => {
    const upload = vi.fn();
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": { ...metadata, available: false,
      source_format: null, source_sha256: null, unavailable_reason: "not_available_locally", reconstruction: null } }));
    renderSource(upload);
    expect(await screen.findByRole("heading", { name: "The original file is unavailable locally" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload new version" }));
    expect(upload).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Download original" })).not.toBeInTheDocument();
  });

  it("discards a late original download after leaving the reader", async () => {
    let finish!: (blob: Blob) => void;
    vi.spyOn(api, "getBlob").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const create = vi.fn();
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": metadata,
      "/rubrics/5/document/paragraphs": { ...metadata, content } }));
    const rendered = renderSource();
    fireEvent.click(await screen.findByRole("button", { name: "Download original" }));
    await waitFor(() => expect(api.getBlob).toHaveBeenCalledOnce());
    rendered.unmount();
    await act(async () => finish(new Blob(["original"], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" })));
    expect(create).not.toHaveBeenCalled();
  });

  it("reads visible source images in sequence and retires queued reads after leaving", async () => {
    const observers: IntersectionObserverCallback[] = [];
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: IntersectionObserverCallback) { observers.push(callback); }
      observe() {} disconnect() {}
    });
    const pending: Array<(blob: Blob) => void> = [];
    const blobs = vi.spyOn(api, "getBlob").mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn(() => "blob:format-source");
      static revokeObjectURL = vi.fn();
    });
    const images = [0, 1, 2].map((image_index) => ({ image_index, paragraph: 7, available: true,
      source_alt_text: `Uploaded diagram ${image_index}`, width_px: 8, height_px: 8,
      unavailable_reason: null, figure_reading: null }));
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": metadata,
      "/rubrics/5/document/paragraphs": { ...metadata, content: { ...content, images } } }));
    const rendered = renderSource();
    await waitFor(() => expect(observers).toHaveLength(3));
    act(() => observers.forEach((callback) => callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)));
    await waitFor(() => expect(blobs).toHaveBeenCalledTimes(1));
    expect(blobs.mock.calls[0][0]).toBe(`/rubrics/5/document/images/0?source_sha256=${hash}`);
    await act(async () => pending[0](new Blob(["image"], { type: "image/png" })));
    await waitFor(() => expect(blobs).toHaveBeenCalledTimes(2));
    expect(blobs.mock.calls[1][0]).toBe(`/rubrics/5/document/images/1?source_sha256=${hash}`);
    rendered.unmount();
    await act(async () => pending[1](new Blob(["image"], { type: "image/png" })));
    expect(blobs).toHaveBeenCalledTimes(2);
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("queues an original download behind its pending DOCX preview without competing requests", async () => {
    let finish!: (response: Response) => void;
    const fallback = stubFetchByPath({ "/rubrics/5/document": metadata });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      new URL(input.toString(), "http://localhost").pathname.endsWith("/paragraphs")
        ? new Promise<Response>((resolve) => { finish = resolve; }) : fallback(input, init)));
    const blobs = vi.spyOn(api, "getBlob").mockResolvedValue(new Blob(["wrong MIME"], { type: "text/plain" }));
    renderSource();
    fireEvent.click(await screen.findByRole("button", { name: "Download original" }));
    await act(async () => { await Promise.resolve(); });
    expect(blobs).not.toHaveBeenCalled();
    await act(async () => finish(Response.json({ ...metadata, content })));
    await waitFor(() => expect(blobs).toHaveBeenCalledOnce());
    expect(await screen.findByRole("alert")).toHaveTextContent("The original could not be downloaded");
    expect(screen.getByText("The actual uploaded requirement")).toBeInTheDocument();
  });

  it("loads PDF bytes through the same queue without starting a second URL fetch", async () => {
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
    vi.stubGlobal("fetch", stubFetchByPath({ "/rubrics/5/document": { ...metadata,
      source_format: "pdf", reconstruction: "original_pdf" } }));
    let finish!: (blob: Blob) => void;
    const blobs = vi.spyOn(api, "getBlob").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    pdf.getDocument.mockImplementation(() => ({ destroy: vi.fn(), promise: Promise.resolve({ numPages: 2,
      getPage: async () => ({ rotate: 0, getViewport: () => ({ width: 600, height: 800 }) }) }) }));
    renderSource();
    await waitFor(() => expect(blobs).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Download original" }));
    await act(async () => { await Promise.resolve(); });
    expect(blobs).toHaveBeenCalledTimes(1);
    const body = new Blob([], { type: "application/pdf" });
    Object.defineProperty(body, "arrayBuffer", { value: async () => new Uint8Array([37, 80, 68, 70]).buffer });
    await act(async () => finish(body));
    await screen.findByLabelText("Page 2 of 2");
    expect(pdf.getDocument).toHaveBeenCalledWith({ data: new Uint8Array([37, 80, 68, 70]) });
    expect(blobs).toHaveBeenCalledTimes(2);
    expect(blobs.mock.calls[0][0]).toContain("/document/file?");
    expect(blobs.mock.calls[1][0]).toContain("/document/original?");
  });
});
