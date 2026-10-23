// Read-only, tokenized report sharing (F8.7, V-040) -- screens 4k/4l.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { retryApiRead } from "../api/queryClient";
import { SHARED_REPORT_READ_RETRIES } from "../config/network";
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
// isolation is a real class of bug backend review has found live in
// this app before).
export function sharedReportQueryOptions(token: string) {
  return {
    queryKey: ["shared-report", token],
    queryFn: () => api.get<SharedReportOut>(`/shared/${token}/report`),
    retry: (failureCount: number, error: unknown) => retryApiRead(failureCount, error, SHARED_REPORT_READ_RETRIES),
  };
}

export function useSharedReport(token: string) {
  return useQuery(sharedReportQueryOptions(token));
}
