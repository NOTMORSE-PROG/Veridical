/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { prepareWorkspaceSource } from "./workspacePreparation";

afterEach(() => vi.unstubAllGlobals());
function sourceFetch() {
  vi.stubGlobal("crypto", webcrypto);
  return vi.fn(async (input: RequestInfo | URL) => new Response(readFileSync(resolve(`public${String(input)}`))));
}

describe("prepared source loading", () => {
  it("verifies the exact original, all pages and all eight source excerpts", async () => {
    const stages: string[] = [];
    const network = sourceFetch();
    await expect(prepareWorkspaceSource(network, new AbortController().signal, key => stages.push(key))).resolves.toMatchObject({ pageCount: 109, excerptsBound: 8 });
    expect(stages).toEqual(["ingesting", "structural", "integrity", "aggregating"]);
    expect(network).toHaveBeenCalledTimes(2);
    expect(network.mock.calls.every(([url]) => String(url).startsWith("/sample-assets/"))).toBe(true);
  });

  it("stops on a changed original before claiming the source is verified", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const stages: string[] = [];
    await expect(prepareWorkspaceSource(vi.fn(async () => new Response("changed file")), new AbortController().signal, key => stages.push(key))).rejects.toThrow("checksum");
    expect(stages).toEqual(["ingesting"]);
  });

  it("rejects a missing original and an incomplete page index", async () => {
    await expect(prepareWorkspaceSource(vi.fn(async () => new Response("", { status: 404 })), new AbortController().signal, () => {})).rejects.toThrow("could not be loaded");
    const network = sourceFetch();
    network.mockImplementationOnce(async () => new Response(readFileSync(resolve("public/sample-assets/stock-inventory-capstone.pdf"))));
    network.mockImplementationOnce(async () => new Response("[]"));
    await expect(prepareWorkspaceSource(network, new AbortController().signal, () => {})).rejects.toThrow("page index is incomplete");
  });

  it("rejects unbound observations and honors cancellation before proceeding", async () => {
    const network = sourceFetch();
    network.mockImplementationOnce(async () => new Response(readFileSync(resolve("public/sample-assets/stock-inventory-capstone.pdf"))));
    network.mockImplementationOnce(async () => new Response(JSON.stringify(Array.from({ length: 109 }, (_, i) => ({ page: i + 1, text: "" })))));
    await expect(prepareWorkspaceSource(network, new AbortController().signal, () => {})).rejects.toThrow("could not be matched");
    const controller = new AbortController();
    controller.abort();
    const stages: string[] = [];
    await expect(prepareWorkspaceSource(sourceFetch(), controller.signal, key => stages.push(key))).rejects.toThrow();
    expect(stages).toEqual(["ingesting"]);
  });
});
