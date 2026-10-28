// Read-only, tokenized report sharing (F8.7, V-040) -- screens 4k/4l.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { useAuthOwner } from "../auth/queryOwner";
import { retryApiRead } from "../api/queryClient";
import { SHARED_REPORT_READ_RETRIES } from "../config/network";
import type { CreateShareLinkIn, SharedReportOut, ShareLinkOut } from "../api/types";

function decodeShareLink(value: unknown, checkRunId: number): ShareLinkOut {
  const link = value as Partial<ShareLinkOut> | null;
  const validDate = (date: unknown) => typeof date === "string" && date.trim().length > 0 && Number.isFinite(Date.parse(date));
  if (!link || typeof link !== "object" || !Number.isInteger(link.check_run_id) || link.check_run_id !== checkRunId
    || checkRunId <= 0 || typeof link.token !== "string" || !link.token.trim() || !validDate(link.created_at)
    || (link.expires_at !== null && !validDate(link.expires_at))) {
    throw new ApiError(502, "invalid_response", "The response did not confirm this report's link. Check link status before trying again.");
  }
  return link as ShareLinkOut;
}

function shareLinkQueryOptions(checkRunId: number) {
  return {
    queryKey: ["share", checkRunId],
    queryFn: async ({ signal }: { signal: AbortSignal }) => {
      const link = await api.get<ShareLinkOut | null>(`/check-runs/${checkRunId}/share`, { signal });
      return link === null ? null : decodeShareLink(link, checkRunId);
    },
  };
}

export function useShareLink(checkRunId: number) {
  return useQuery(shareLinkQueryOptions(checkRunId));
}

async function reconcileShareLink(client: ReturnType<typeof useQueryClient>, request: { runId: number; owner: ReturnType<typeof useAuthOwner> }) {
  if (!request.owner.isCurrent()) return;
  await client.cancelQueries({ queryKey: ["share", request.runId], exact: true });
  if (!request.owner.isCurrent()) return;
  const options = shareLinkQueryOptions(request.runId);
  await client.fetchQuery({ ...options, staleTime: 0, queryFn: async context => {
    if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before checking link status.");
    const link = await options.queryFn(context);
    if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before checking link status.");
    return link;
  } });
}

export function useCreateShareLink(checkRunId: number) {
  const queryClient = useQueryClient();
  const owner = useAuthOwner();
  const capture = (body: CreateShareLinkIn) => ({ runId: checkRunId, body: { ...body }, owner });
  const mutation = useMutation({
    mutationFn: async (request: ReturnType<typeof capture>) => {
      if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before sharing this report.");
      const link = await api.post<ShareLinkOut>(`/check-runs/${request.runId}/share`, request.body);
      return decodeShareLink(link, request.runId);
    },
    onSuccess: (_link, request) => reconcileShareLink(queryClient, request),
  });
  return { ...mutation,
    mutate: (body: CreateShareLinkIn, options?: Parameters<typeof mutation.mutate>[1]) => mutation.mutate(capture(body), options),
    mutateAsync: (body: CreateShareLinkIn, options?: Parameters<typeof mutation.mutateAsync>[1]) => mutation.mutateAsync(capture(body), options),
  };
}

export function useRevokeShareLink(checkRunId: number) {
  const queryClient = useQueryClient();
  const owner = useAuthOwner();
  const capture = () => ({ runId: checkRunId, owner });
  const mutation = useMutation({
    mutationFn: async (request: ReturnType<typeof capture>) => {
      if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before changing this link.");
      const out = await api.delete<{ ok: boolean }>(`/check-runs/${request.runId}/share`);
      if (out?.ok !== true) throw new ApiError(502, "invalid_response", "The response did not confirm revocation. Check link status before trying again.");
      return out;
    },
    onSuccess: (_out, request) => reconcileShareLink(queryClient, request),
  });
  return { ...mutation,
    mutate: (_input?: undefined, options?: Parameters<typeof mutation.mutate>[1]) => mutation.mutate(capture(), options),
    mutateAsync: (_input?: undefined, options?: Parameters<typeof mutation.mutateAsync>[1]) => mutation.mutateAsync(capture(), options),
  };
}

// The public read (screen 4l) -- a fully separate query-key namespace
// from every authenticated key above, so the public fetch can never
// share a cache slot with an authenticated one (TanStack Query session-
// isolation is a real class of bug backend review has found live in
// this app before).
export function sharedReportQueryOptions(token: string) {
  return {
    queryKey: ["shared-report", token],
    queryFn: ({ signal }: { signal: AbortSignal }) => api.getPublic<SharedReportOut>(`/shared/${encodeURIComponent(token)}/report`, { signal }),
    retry: (failureCount: number, error: unknown) => retryApiRead(failureCount, error, SHARED_REPORT_READ_RETRIES),
  };
}

export function useSharedReport(token: string) {
  return useQuery(sharedReportQueryOptions(token));
}
