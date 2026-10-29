import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadParts, type SignedPart } from "./multipartUpload";

vi.mock("../config/ui", () => ({ UPLOAD_PART_RETRY_DELAY_MS: 0 }));

class FakeRequest {
  static open = 0;
  static mostOpen = 0;
  static sent: { url: string; size: number }[] = [];
  static failures = new Map<string, number>();
  static delays = new Map<string, number>();
  static aborted: string[] = [];
  upload: { onprogress: ((event: { loaded: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  url = "";
  private timer: ReturnType<typeof setTimeout> | undefined;

  abort() {
    clearTimeout(this.timer);
    FakeRequest.open -= 1;
    FakeRequest.aborted.push(this.url);
    this.onabort?.();
  }

  open(_method: string, url: string) { this.url = url; }

  setRequestHeader() {}

  send(body: Blob) {
    FakeRequest.open += 1;
    FakeRequest.mostOpen = Math.max(FakeRequest.mostOpen, FakeRequest.open);
    this.timer = setTimeout(() => {
      this.upload.onprogress?.({ loaded: Math.floor(body.size / 2) });
      FakeRequest.open -= 1;
      const remaining = FakeRequest.failures.get(this.url) ?? 0;
      if (remaining > 0) {
        FakeRequest.failures.set(this.url, remaining - 1);
        this.onerror?.();
        return;
      }
      FakeRequest.sent.push({ url: this.url, size: body.size });
      this.status = 200;
      this.onload?.();
    }, FakeRequest.delays.get(this.url) ?? 1);
  }
}

const file = (bytes: number) => new File([new Uint8Array(bytes)], "thesis.pdf", { type: "application/pdf" });

const plan = (sizes: number[], done: number[] = []): SignedPart[] => sizes.map((size, index) => ({
  number: index + 1,
  size,
  done: done.includes(index + 1),
  ...(done.includes(index + 1) ? {} : { url: `https://storage.example/part-${index + 1}` }),
}));

beforeEach(() => {
  FakeRequest.open = 0;
  FakeRequest.mostOpen = 0;
  FakeRequest.sent = [];
  FakeRequest.failures = new Map();
  FakeRequest.delays = new Map();
  FakeRequest.aborted = [];
  vi.stubGlobal("XMLHttpRequest", FakeRequest);
});

afterEach(() => { vi.unstubAllGlobals(); });

describe("parallel part transfer", () => {
  it("sends each part's own slice, never more than the allowed number at once", async () => {
    const percents: number[] = [];
    await uploadParts(file(23), { parts: plan([5, 5, 5, 5, 3]), concurrency: 2, retries: 0 },
      (percent) => percents.push(percent));
    expect(FakeRequest.sent.map((part) => part.size).sort()).toEqual([3, 5, 5, 5, 5]);
    expect(new Set(FakeRequest.sent.map((part) => part.url)).size).toBe(5);
    expect(FakeRequest.mostOpen).toBe(2);
    expect(Math.max(...percents)).toBe(99);
  });

  it("skips parts storage already holds when an attempt resumes", async () => {
    const percents: number[] = [];
    await uploadParts(file(15), { parts: plan([5, 5, 5], [1, 3]), concurrency: 4, retries: 0 },
      (percent) => percents.push(percent));
    expect(FakeRequest.sent).toEqual([{ url: "https://storage.example/part-2", size: 5 }]);
    expect(percents[0]).toBe(66);
  });

  it("resends only the part that failed", async () => {
    FakeRequest.failures.set("https://storage.example/part-2", 2);
    await uploadParts(file(15), { parts: plan([5, 5, 5]), concurrency: 3, retries: 2 });
    expect(FakeRequest.sent.map((part) => part.url).sort()).toEqual([
      "https://storage.example/part-1",
      "https://storage.example/part-2",
      "https://storage.example/part-3",
    ]);
  });

  it("stops taking new parts once a part is out of retries", async () => {
    FakeRequest.failures.set("https://storage.example/part-1", 5);
    await expect(uploadParts(file(25), { parts: plan([5, 5, 5, 5, 5]), concurrency: 1, retries: 1 }))
      .rejects.toMatchObject({ code: "upload_transport" });
    expect(FakeRequest.sent).toEqual([]);
  });

  it("cancels the other parts in flight once one part is out of retries", async () => {
    FakeRequest.failures.set("https://storage.example/part-1", 5);
    FakeRequest.delays.set("https://storage.example/part-2", 200);
    await expect(uploadParts(file(10), { parts: plan([5, 5]), concurrency: 2, retries: 0 }))
      .rejects.toMatchObject({ code: "upload_transport" });
    expect(FakeRequest.aborted).toEqual(["https://storage.example/part-2"]);
    expect(FakeRequest.sent).toEqual([]);
    expect(FakeRequest.open).toBe(0);
  });

  it("refuses a part that arrives without a signed address", async () => {
    await expect(uploadParts(file(5), { parts: [{ number: 1, size: 5, done: false }], concurrency: 1, retries: 3 }))
      .rejects.toMatchObject({ code: "upload_transport" });
  });
});
