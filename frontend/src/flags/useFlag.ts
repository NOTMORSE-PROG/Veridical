// Flag evidence/annotation/override data layer (F8.2/F8.4, screen 4i).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { FlagOut, OverrideFlagOut } from "../api/types";

export function useFlag(flagId: number) {
  return useQuery({
    queryKey: ["flag", flagId],
    queryFn: () => api.get<FlagOut>(`/flags/${flagId}`),
  });
}

export function useAnnotateFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (annotation: string) =>
      api.post<FlagOut>(`/flags/${flagId}/annotate`, { annotation }),
    onSuccess: (out) => queryClient.setQueryData(["flag", flagId], out),
  });
}

export function useOverrideFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) =>
      api.post<OverrideFlagOut>(`/flags/${flagId}/override`, { reason }),
    onSuccess: (out) => {
      queryClient.setQueryData(["flag", flagId], out);
      queryClient.setQueryData(["report", out.report.check_run_id], out.report);
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
