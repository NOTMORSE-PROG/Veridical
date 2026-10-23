function retryLimit(value: string | undefined, fallback: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

/** Automatic read retries; instructor actions are never automatically repeated. */
export const API_READ_RETRIES = retryLimit(import.meta.env.VITE_API_READ_RETRIES, 3);
export const SHARED_REPORT_READ_RETRIES = retryLimit(import.meta.env.VITE_SHARED_REPORT_READ_RETRIES, 2);
