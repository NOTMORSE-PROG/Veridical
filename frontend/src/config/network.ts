function retryLimit(value: string | undefined, fallback: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

/** Automatic read retries; instructor actions are never automatically repeated. */
export const API_READ_RETRIES = retryLimit(import.meta.env.VITE_API_READ_RETRIES, 3);
export const SHARED_REPORT_READ_RETRIES = retryLimit(import.meta.env.VITE_SHARED_REPORT_READ_RETRIES, 2);

/** BUG-563: how long a report-page read counts as fresh. Without it every
 * panel that mounts after the first answer refetched the report, and each
 * report read parses the stored extraction on the server (BUG-561). Writes
 * still invalidate these reads at once. */
export const REPORT_READ_FRESH_MS = retryLimit(import.meta.env.VITE_REPORT_READ_FRESH_MS, 30_000);
