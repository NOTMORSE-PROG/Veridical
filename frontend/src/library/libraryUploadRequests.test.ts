import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { StaleUploadContextError, UnconfirmedUploadError, UploadSessionUnavailableError } from "../api/uploadOutcome";
import type { UploadAttemptScope } from "../auth/useUploadScope";
import { decideOwnedLibraryEntry, removeOwnedLibraryEntry, submitOwnedLibraryEntry } from "./libraryUploadRequests";

function attempt() {
  let current = true;
  const scope: UploadAttemptScope = {
    actorId: 1, isCurrent: () => current, verify: vi.fn(async () => {}),
    rejectActor: vi.fn(() => { current = false; }),
  };
  return { scope, retire: () => { current = false; } };
}
const reply = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
afterEach(() => vi.unstubAllGlobals());

describe("owned Library request boundaries", () => {
  it("waits for fresh preflight before admitting the exact file and preserves browser multipart/auth semantics", async () => {
    const { scope } = attempt();
    let release!: () => void;
    vi.mocked(scope.verify).mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
    const admitted = vi.fn();
    const fetcher = vi.fn(async () => reply({ outcome: "added" }));
    vi.stubGlobal("fetch", fetcher);
    const file = new File(["synthetic"], "first.docx");
    const request = submitOwnedLibraryEntry({ file, title: "Own title", schoolYear: "2025-2026", authors: [" One ", "Two"] }, scope, admitted);
    expect(admitted).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
    release();
    await request;
    expect(scope.verify).toHaveBeenCalledTimes(2);
    expect(admitted).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/library/entries");
    expect(init.credentials).toBe("include");
    const headers = new Headers(init.headers);
    expect(headers.get("X-Veridical-Expected-Instructor")).toBe("1");
    expect(headers.has("Content-Type")).toBe(false);
    expect((init.body as FormData).get("file")).toBe(file);
    expect((init.body as FormData).getAll("authors")).toEqual(["One", "Two"]);
    expect((init.body as FormData).get("school_year")).toBe("2025-2026");
  });

  it("does not send after preflight is cancelled or becomes unavailable", async () => {
    const { scope, retire } = attempt();
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    vi.mocked(scope.verify).mockRejectedValueOnce(new UploadSessionUnavailableError());
    await expect(submitOwnedLibraryEntry({ file: new File([], "unsent.pdf") }, scope)).rejects.toBeInstanceOf(UploadSessionUnavailableError);
    vi.mocked(scope.verify).mockImplementationOnce(async () => { retire(); });
    await expect(removeOwnedLibraryEntry(21, scope)).rejects.toBeInstanceOf(StaleUploadContextError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects a late response after the operation loses ownership", async () => {
    const { scope, retire } = attempt();
    vi.stubGlobal("fetch", vi.fn(async () => { retire(); return reply({ manuscript_id: 21, status: "active" }); }));
    await expect(decideOwnedLibraryEntry(21, "add", scope)).rejects.toBeInstanceOf(StaleUploadContextError);
    expect(scope.verify).toHaveBeenCalledTimes(1);
  });

  it("keeps a possibly committed result unconfirmed when postflight cannot establish the session", async () => {
    const { scope } = attempt();
    vi.mocked(scope.verify).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new UploadSessionUnavailableError());
    vi.stubGlobal("fetch", vi.fn(async () => reply({ manuscript_id: 21, status: "removed" })));
    await expect(removeOwnedLibraryEntry(21, scope)).rejects.toBeInstanceOf(UnconfirmedUploadError);
  });

  it("preserves a definite busy rejection when the subsequent session read is unavailable", async () => {
    const { scope } = attempt();
    vi.mocked(scope.verify).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new UploadSessionUnavailableError());
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "document_preparation_busy", message: "Preparation is busy." } }), { status: 503 })));
    await expect(submitOwnedLibraryEntry({ file: new File([], "busy.pdf") }, scope)).rejects.toMatchObject({ status: 503, code: "document_preparation_busy" });
  });

  it("retires an actor-mismatched response without treating it as a content rejection", async () => {
    const { scope } = attempt();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "actor_context_changed", message: "Account changed." } }), { status: 409 })));
    await expect(submitOwnedLibraryEntry({ file: new File([], "old.pdf") }, scope)).rejects.toBeInstanceOf(StaleUploadContextError);
    expect(scope.rejectActor).toHaveBeenCalledTimes(1);
    expect(scope.verify).toHaveBeenCalledTimes(1);
  });

  it.each([{ manuscript_id: 22, status: "active" }, { manuscript_id: 21, status: "removed" }, null])("does not acknowledge a mismatched duplicate decision: %s", async value => {
    const { scope } = attempt();
    vi.stubGlobal("fetch", vi.fn(async () => reply(value)));
    await expect(decideOwnedLibraryEntry(21, "add", scope)).rejects.toBeInstanceOf(UnconfirmedUploadError);
  });

  it("keeps a definite current-owner content error and never starts another POST", async () => {
    const { scope } = attempt();
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ error: { code: "file_malformed", message: "Unreadable file." } }), { status: 422 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(submitOwnedLibraryEntry({ file: new File([], "bad.pdf") }, scope)).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(scope.verify).toHaveBeenCalledTimes(2);
  });
});
