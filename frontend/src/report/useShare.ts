// Read-only, tokenized report sharing (F8.7, V-040) -- screens 4k/4l.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { CreateShareLinkIn, SharedReportOut, ShareLinkOut } from "../api/types";

export function useShareLink(checkRunId: number) {
  return useQuery({
    queryKey: ["share", checkRunId],
    queryFn: () => api.get<ShareLinkOut | null>(`/check-runs/${checkRunId}/share`),
  });
}

export function useCreateShareLink(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateShareLinkIn) =>
      api.post<ShareLinkOut>(`/check-runs/${checkRunId}/share`, body),
    onSuccess: (link) => {
      queryClient.setQueryData(["share", checkRunId], link);
    },
  });
}

export function useRevokeShareLink(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<{ ok: boolean }>(`/check-runs/${checkRunId}/share`),
    onSuccess: () => {
      queryClient.setQueryData(["share", checkRunId], null);
    },
  });
}

// The public read (screen 4l) -- a fully separate query-key namespace
// from every authenticated key above, so the public fetch can never
// share a cache slot with an authenticated one (TanStack Query session-
// isolation is a real class of bug `backend-critic` has found live in
// this app before).
export function useSharedReport(token: string) {
  return useQuery({
    queryKey: ["shared-report", token],
    queryFn: () => api.get<SharedReportOut>(`/shared/${token}/report`),
    // A "not_found"/"gone" response is a TERMINAL state -- it will never
    // succeed on retry, so React Query's default (3 retries, exponential
    // backoff) would delay an honest "this link doesn't exist" message
    // by several seconds for no reason (same narrow error-code-branching
    // precedent useReport.ts's own refetchReportOnConflict already uses).
    retry: (failureCount, error) => {
      if (error instanceof ApiError && (error.code === "not_found" || error.code === "gone")) {
        return false;
      }
      return failureCount < 2;
    },
  });
}
