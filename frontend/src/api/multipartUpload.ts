// F1/F9: send a large manuscript to private storage in parallel parts. Each
// part has its own signed URL bound to its exact size. A failed part is sent
// again on its own instead of restarting the whole file, and progress counts
// the bytes every part has sent so far.
import { ApiError } from "./client";
import { UPLOAD_PART_RETRY_DELAY_MS } from "../config/ui";

export interface SignedPart {
  number: number;
  size: number;
  done: boolean;
  url?: string;
}

export interface PartPlan {
  parts: SignedPart[];
  concurrency: number;
  retries: number;
}

const TRANSFER_FAILED = "The file transfer did not finish. Retry this upload to check its saved status.";

function putPart(url: string, body: Blob, onSent: (bytes: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    request.upload.onprogress = (event) => onSent(event.loaded);
    request.onload = () => (request.status >= 200 && request.status < 300
      ? resolve()
      : reject(new ApiError(request.status, "upload_transport", TRANSFER_FAILED)));
    request.onerror = () => reject(new ApiError(0, "upload_transport", TRANSFER_FAILED));
    request.send(body);
  });
}

export async function uploadParts(file: File, plan: PartPlan, onProgress?: (percent: number) => void): Promise<void> {
  // Every part except the last has the first part's size (a storage rule).
  const partSize = plan.parts[0]?.size ?? file.size;
  const sent = new Map<number, number>();
  for (const part of plan.parts) if (part.done) sent.set(part.number, part.size);
  const report = () => {
    if (!onProgress || file.size === 0) return;
    let total = 0;
    for (const bytes of sent.values()) total += bytes;
    onProgress(Math.min(99, Math.floor((total / file.size) * 100)));
  };
  const waiting = plan.parts.filter((part) => !part.done);
  let failed = false;

  const sendPart = async (part: SignedPart) => {
    if (!part.url) throw new ApiError(0, "upload_transport", TRANSFER_FAILED);
    const start = (part.number - 1) * partSize;
    const body = file.slice(start, start + part.size);
    for (let attempt = 0; ; attempt += 1) {
      try {
        await putPart(part.url, body, (bytes) => { sent.set(part.number, bytes); report(); });
        sent.set(part.number, part.size);
        report();
        return;
      } catch (error) {
        sent.set(part.number, 0);
        report();
        if (attempt >= plan.retries || failed) throw error;
        await new Promise((resolve) => setTimeout(resolve, UPLOAD_PART_RETRY_DELAY_MS * (attempt + 1)));
      }
    }
  };

  const worker = async () => {
    for (let part = waiting.shift(); part && !failed; part = waiting.shift()) {
      try {
        await sendPart(part);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };

  report();
  const lanes = Math.max(1, Math.min(plan.concurrency, waiting.length));
  await Promise.all(Array.from({ length: lanes }, worker));
}
