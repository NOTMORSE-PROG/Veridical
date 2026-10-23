import { QueryClient } from "@tanstack/react-query";
import { API_READ_RETRIES } from "../config/network";
import { ApiError } from "./client";

export function retryApiRead(failureCount: number, error: unknown, limit = API_READ_RETRIES): boolean {
  // A definitive denied/missing/invalid read must reach the rendering gate
  // immediately, including when the query has previously cached private data.
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < limit;
}

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: retryApiRead }, mutations: { retry: false } },
  });
}
