// V-042 (screen 4u) — read-only thresholds + live prompt/model version
// transparency. Quota detail reuses the existing `useQuota()`; profile
// reuses `useMe()` -- neither is duplicated here.
// V-052 (BYOK): the two mutations below share this same `["settings"]`
// query key -- both endpoints return the full `SettingsOut`, so a
// successful save/delete updates the cache directly instead of a
// refetch, matching `useShare.ts`'s own `setQueryData` convention.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SetApiKeyIn, SettingsOut } from "../api/types";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingsOut>("/settings"),
  });
}

export function useSetApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SetApiKeyIn) => api.post<SettingsOut>("/settings/api-key", body),
    onSuccess: (settings) => {
      queryClient.setQueryData(["settings"], settings);
    },
  });
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<SettingsOut>("/settings/api-key"),
    onSuccess: (settings) => {
      queryClient.setQueryData(["settings"], settings);
    },
  });
}
