import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import type { EvidenceSourceRequest } from "../report/evidenceTypes";
import { RecordedEvidenceReader } from "./RecordedEvidenceReader";

const observed = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));
vi.mock("./DocxPane", () => ({ DocxPane: (props: Record<string, unknown>) => {
  observed.props = props;
  return <div>Bound extracted source</div>;
} }));
vi.mock("./PdfPane", () => ({ PdfPane: () => <div>Bound PDF source</div> }));

const identity = "d".repeat(64);
const request: EvidenceSourceRequest = { snapshotId: "a".repeat(64), actionId: "source-action",
  reference: { reference_id: "b".repeat(64), criterion_id: 3, flag_id: null, role: "criterion_evidence", index: 0,
    quote: "Saved excerpt", anchor: "paragraph 2", source_scope: "own_manuscript", source_state: "not_checked" } };

function setup(locationFailure?: () => Response | undefined) {
  observed.props = {};
  const images: Array<{ signal?: AbortSignal | null; release: (response: Response) => void }> = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.includes("/source-images/")) return await new Promise<Response>(release => images.push({ signal: init?.signal, release }));
    if (url.pathname.includes("/location")) {
      const failure = locationFailure?.();
      if (failure) return failure;
      const second = url.searchParams.get("snapshot_id") === "e".repeat(64);
      return new Response(JSON.stringify({ snapshot_id: second ? "e".repeat(64) : request.snapshotId,
        source_identity: identity, source_format: "docx", caption: "Recorded paragraph location.",
        placement: { reference_id: second ? "f".repeat(64) : request.reference.reference_id,
          state: "paragraph_only", paragraph: second ? 3 : 2, page: null, boxes: [], reason: null } }));
    }
    return new Response(JSON.stringify({ source_identity: identity, document: {
      paragraphs: [{ paragraph: 2, text: "Retained source text", heading_level: null }], tables: [], images: [],
    } }));
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  const props = { run: 7, request, current: true, visible: true, attempt: 1, canPlace: () => false, onManual: () => {} };
  const view = render(<RecordedEvidenceReader {...props} />, {
    wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
  return { props, view, fetch, images };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("explains temporary source admission failure and permits a deliberate verified retry", async () => {
  let busy = true;
  setup(() => busy ? new Response(JSON.stringify({error: {code: "rate_limited", message: "Source images are busy. Try again shortly."}}), {status: 429}) : undefined);
  expect(await screen.findByText(/Source viewing is temporarily unavailable.*Source images are busy/)).toBeVisible();
  expect(screen.queryByText("This recorded location could not be verified.")).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Continue in full manuscript"})).toBeEnabled();
  busy = false;
  fireEvent.click(screen.getByRole("button", {name: "Retry recorded location"}));
  expect(await screen.findByText("Recorded paragraph location.")).toBeVisible();
  await waitFor(() => expect(observed.props.requestedParagraph).toBe(2));
});

it("retires placement while retaining the same reader through grading and a fresh location", async () => {
  const { props, view, fetch } = setup();
  await waitFor(() => expect(observed.props.paragraphs).toHaveLength(1));
  expect(observed.props.requestedParagraph).toBe(2);
  const loader = observed.props.loadImage;
  const source = observed.props.paragraphs;
  view.rerender(<RecordedEvidenceReader {...props} current={false} />);
  expect(observed.props.requestedParagraph).toBeNull();
  expect(observed.props.paragraphs).toBe(source);
  expect(screen.getByText(/Source marks are cleared/)).toBeVisible();
  view.rerender(<RecordedEvidenceReader {...props} attempt={2} request={{ ...request,
    snapshotId: "e".repeat(64), reference: { ...request.reference, reference_id: "f".repeat(64) } }} />);
  await waitFor(() => expect(observed.props.requestedParagraph).toBe(3));
  expect(observed.props.loadImage).toBe(loader);
  expect(observed.props.paragraphs).toBe(source);
  expect(fetch.mock.calls.filter(([url]) => String(url).includes("source-document"))).toHaveLength(1);
  expect(observed.props.sourceQuery).toBe(`source_identity=${identity}`);
  expect(observed.props.imageBasePath).toBe("/check-runs/7/evidence/source-images");
});

it("serializes source image reads and retires queued images on reader removal", async () => {
  const { view, images } = setup();
  await waitFor(() => expect(observed.props.paragraphs).toHaveLength(1));
  const load = observed.props.loadImage as (path: string) => Promise<Blob>;
  const done = Promise.allSettled([
    load(`/check-runs/7/evidence/source-images/0?source_identity=${identity}`),
    load(`/check-runs/7/evidence/source-images/1?source_identity=${identity}`),
  ]);
  await waitFor(() => expect(images).toHaveLength(1));
  view.unmount();
  expect(images[0].signal?.aborted).toBe(true);
  await act(async () => images[0].release(new Response("old image", {
    headers: { "Content-Type": "image/png", "X-Evidence-Source-Identity": identity },
  })));
  const out = await done;
  expect(out.map(result => result.status)).toEqual(["rejected", "rejected"]);
  expect(images).toHaveLength(1);
  await expect(load(`/check-runs/7/evidence/source-images/2?source_identity=${identity}`)).rejects.toMatchObject({ status: 409 });
  expect(images).toHaveLength(1);
});
