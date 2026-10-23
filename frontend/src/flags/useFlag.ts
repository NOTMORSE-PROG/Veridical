// Flag evidence/annotation/override data layer (F8.2/F8.4, screen 4i).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConfirmCitationSourceOut, FlagOut, OverrideFlagOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";

function flagMutationOwner(client: ReturnType<typeof useQueryClient>, flagId: number) {
  const authQuery = client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true });
  const instructorId = client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id;
  const runId = client.getQueryData<FlagOut>(["flag", flagId])?.check_run_id;
  return { accepts: (out: FlagOut) => authQuery !== undefined && instructorId !== undefined
    && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === authQuery
    && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === instructorId
    && out?.id === flagId && runId !== undefined && out.check_run_id === runId };
}

export function useFlag(flagId: number) {
  return useQuery({
    queryKey: ["flag", flagId],
    queryFn: () => api.get<FlagOut>(`/flags/${flagId}`),
    enabled: Number.isInteger(flagId) && flagId > 0,
  });
}

export function useAnnotateFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: (annotation: string) =>
      api.post<FlagOut>(`/flags/${flagId}/annotate`, { annotation }),
    onSuccess: (out, _input, owner) => { if (owner?.accepts(out)) queryClient.setQueryData(["flag", flagId], out); },
  });
}

export function useOverrideFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: (reason: string) =>
      api.post<OverrideFlagOut>(`/flags/${flagId}/override`, { reason }),
    onSuccess: (out, _input, owner) => {
      if (!owner?.accepts(out) || out.report?.check_run_id !== out.check_run_id) return;
      queryClient.setQueryData(["flag", flagId], out);
      queryClient.setQueryData(["report", out.report.check_run_id], out.report);
      // BUG-033: the report's flags panel is its own query
      // (["report", id, "flags"], useReport.ts's useFlags) — without
      // this, an instructor who overrides here and clicks "View updated
      // report" would see a stale ACTIVE row in that panel until a
      // manual refetch, reintroducing the exact "overridden must not
      // read the same as active" failure this ticket exists to fix, one
      // layer up (design review spec, required not optional).
      queryClient.invalidateQueries({ queryKey: ["report", out.report.check_run_id, "flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

// BUG-078: byte-identical onSuccess shape to useOverrideFlag (same three
// setQueryData/invalidateQueries calls) — reusing it means the report's
// flags panel correctly stops showing this flag as active, for free.
export function useConfirmCitationSource(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: (reason: string) =>
      api.post<ConfirmCitationSourceOut>(`/flags/${flagId}/confirm-source`, { reason }),
    onSuccess: (out, _input, owner) => {
      if (!owner?.accepts(out) || out.report?.check_run_id !== out.check_run_id) return;
      queryClient.setQueryData(["flag", flagId], out);
      queryClient.setQueryData(["report", out.report.check_run_id], out.report);
      queryClient.invalidateQueries({ queryKey: ["report", out.report.check_run_id, "flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
