import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { captureAuthOwner } from "../auth/queryOwner";
import { useEvidenceDocument, useEvidenceLocation, useEvidenceManifest } from "./useEvidenceSource";

const id = "a".repeat(64);
const other = "b".repeat(64);
function Manifest({ revision = 3 }: { revision?: number }) {
  const query = useEvidenceManifest(7, revision);
  return <p>{query.isError ? "rejected manifest" : query.data?.snapshot_id ?? "waiting"}</p>;
}
function Location() {
  const query = useEvidenceLocation(7, id, id, true);
  return <p>{query.isError ? "rejected location" : query.data?.caption ?? "waiting"}</p>;
}
function Document() {
  const query = useEvidenceDocument(7, id, true);
  return <p>{query.isError ? "rejected document" : query.data?.document.paragraphs[0]?.text ?? "waiting"}</p>;
}
function mount(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(ME_QUERY_KEY, { id: 1 });
  const view = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return { client, view };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("recorded evidence query boundaries", () => {
  it("does not send a source request for the immediately retired StrictMode setup", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      snapshot_id: id, source_identity: id, source_format: "pdf",
      placement: { reference_id: id, state: "text_layer_match", page: 7, boxes: [[1, 2, 3, 4]], paragraph: null, reason: null },
      caption: "Verified page seven",
    })));
    vi.stubGlobal("fetch", fetch);
    mount(<StrictMode><Location /></StrictMode>);
    expect(await screen.findByText("Verified page seven")).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(false);
  });

  it("sends nothing when a source observer retires before transport starts", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const { view } = mount(<Location />);
    view.unmount();
    await act(async () => { await Promise.resolve(); });
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([2, 4])("rejects a separately loaded manifest from revision %s", async revision => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      snapshot_id: id, assessment_revision: revision, llm_mode: "real", references: [],
    }))));
    mount(<Manifest />);
    expect(await screen.findByText("rejected manifest")).toBeVisible();
    expect(screen.queryByText(id)).not.toBeInTheDocument();
  });

  it.each(["snapshot", "reference"])("rejects a response with a different %s", async change => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      snapshot_id: change === "snapshot" ? other : id, source_identity: id, source_format: "pdf",
      placement: { reference_id: change === "reference" ? other : id, state: "page_only", page: 1, boxes: [], paragraph: null, reason: null },
      caption: "must not be shown",
    }))));
    mount(<Location />);
    expect(await screen.findByText("rejected location")).toBeVisible();
    expect(screen.queryByText("must not be shown")).not.toBeInTheDocument();
  });

  it("rejects a projected DOCX response from a different source identity", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      source_identity: other, document: { paragraphs: [{ paragraph: 0, text: "wrong source" }] },
    }))));
    mount(<Document />);
    expect(await screen.findByText("rejected document")).toBeVisible();
    expect(screen.queryByText("wrong source")).not.toBeInTheDocument();
  });

  it("retires a delayed private read on same-instructor authentication replacement", async () => {
    const releases: Array<(response: Response) => void> = [];
    const settled: boolean[] = [];
    const fetch = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => {
      const index = releases.length;
      return new Promise<Response>(resolve => { releases.push(resolve); }).then(response => {
        settled[index] = true;
        return response;
      });
    });
    vi.stubGlobal("fetch", fetch);
    const { client } = mount(<Manifest />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const firstOwner = captureAuthOwner(client);
    await act(async () => {
      client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true });
      client.setQueryData(ME_QUERY_KEY, { id: 1 });
    });
    const nextOwner = captureAuthOwner(client);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(nextOwner.lifetime).not.toBe(firstOwner.lifetime);
    expect(firstOwner.isCurrent()).toBe(false);
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: "include" });
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetch.mock.calls[1][1]?.signal?.aborted).toBe(false);
    await act(async () => releases[0](new Response(JSON.stringify({ snapshot_id: id, assessment_revision: 3,
      llm_mode: "real", references: [] }))));
    await waitFor(() => expect(settled[0]).toBe(true));
    expect(client.getQueryData(["report", 7, "evidence", 1, nextOwner.lifetime, 3])).toBeUndefined();
    expect(screen.queryByText(id)).not.toBeInTheDocument();
    await act(async () => releases[1](new Response(JSON.stringify({ snapshot_id: other, assessment_revision: 3,
      llm_mode: "real", references: [] }))));
    expect(await screen.findByText(other)).toBeVisible();
    expect(client.getQueryData(["report", 7, "evidence", 1, nextOwner.lifetime, 3])).toMatchObject({ snapshot_id: other });
    expect(screen.queryByText(id)).not.toBeInTheDocument();
  });
});
