import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { directManuscriptUpload } from "./directManuscriptUpload";
import { StaleUploadContextError } from "./uploadOutcome";

const file = () => new File(["authored acceptance control"], "control.pdf", { type: "application/pdf" });
const scope = () => ({ actorId: 7, isCurrent: () => true, verify: vi.fn().mockResolvedValue(undefined), rejectActor: vi.fn() });
const polling = { poll_after_ms: 1, poll_timeout_ms: 1000 };

afterEach(() => { vi.restoreAllMocks(); });

describe("durable manuscript transport", () => {
  it("relays the selected file once, then polls the same attempt for its receipt", async () => {
    const selected = file();
    let identity = "";
    const result = { manuscript_id: 12 };
    const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => {
      if (path === "/manuscripts/uploads") {
        identity = (body as { attempt_id: string }).attempt_id;
        return { attempt_id: identity, status: "pending", transport: "relay", ...polling } as never;
      }
      expect(path).toBe(`/manuscripts/uploads/${identity}/file`);
      expect((body as FormData).get("file")).toBe(selected);
      return { attempt_id: identity, status: "queued", ...polling } as never;
    });
    const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
      expect(path).toBe(`/manuscripts/uploads/${identity}`);
      return { attempt_id: identity, status: "done", result, ...polling } as never;
    });
    const external = vi.spyOn(api, "putUpload");
    await expect(directManuscriptUpload(selected, scope())).resolves.toEqual(result);
    expect(post).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenCalledTimes(1);
    expect(external).not.toHaveBeenCalled();
  });

  it("reconciles a lost relay response with the original identity instead of sending the file again", async () => {
    const selected = file();
    const owner = scope();
    const identities: string[] = [];
    let starts = 0;
    const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => {
      if (path === "/manuscripts/uploads") {
        identities.push((body as { attempt_id: string }).attempt_id);
        starts += 1;
        return { attempt_id: identities[0], status: starts === 1 ? "pending" : "done",
          transport: "relay", result: starts === 1 ? undefined : { manuscript_id: 13 }, ...polling } as never;
      }
      throw new TypeError("Controlled lost response");
    });
    await expect(directManuscriptUpload(selected, owner)).rejects.toThrow("Controlled lost response");
    await expect(directManuscriptUpload(selected, owner)).resolves.toEqual({ manuscript_id: 13 });
    expect(identities).toHaveLength(2);
    expect(new Set(identities).size).toBe(1);
    expect(post.mock.calls.filter(([path]) => path.endsWith("/file"))).toHaveLength(1);
  });

  it("retires an actor before completing an external transfer", async () => {
    const owner = scope();
    const post = vi.spyOn(api, "post").mockImplementation(async (_path, body) => ({
      attempt_id: (body as { attempt_id: string }).attempt_id, status: "pending", transport: "r2",
      upload_url: "https://private-object.example/temporary", upload_headers: {}, ...polling,
    } as never));
    vi.spyOn(api, "putUpload").mockImplementation(async () => { owner.isCurrent = () => false; });
    await expect(directManuscriptUpload(file(), owner)).rejects.toBeInstanceOf(StaleUploadContextError);
    expect(post).toHaveBeenCalledTimes(1);
    expect(owner.verify).not.toHaveBeenCalled();
  });

  it("withholds a receipt returned after the captured actor retires", async () => {
    const owner = scope();
    vi.spyOn(api, "post").mockImplementation(async (_path, body) => {
      owner.isCurrent = () => false;
      return { attempt_id: (body as { attempt_id: string }).attempt_id,
        status: "done", result: { manuscript_id: 99 }, ...polling } as never;
    });
    await expect(directManuscriptUpload(file(), owner)).rejects.toBeInstanceOf(StaleUploadContextError);
  });
});
