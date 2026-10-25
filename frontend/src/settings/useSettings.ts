// V-042 (screen 4u) — read-only thresholds + live prompt/model version
// transparency. Quota detail reuses the existing `useQuota()`; profile
// reuses `useMe()` -- neither is duplicated here.
// V-052 (BYOK): the two mutations below share this same `["settings"]`
// query key -- both endpoints return the full `SettingsOut`, so a
// successful save/delete updates the cache directly instead of a
// refetch, matching `useShare.ts`'s own `setQueryData` convention.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { SetApiKeyIn, SettingsOut } from "../api/types";
import { useAuthOwner } from "../auth/queryOwner";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingsOut>("/settings"),
  });
}

export function useSetApiKey() {
  const queryClient = useQueryClient();
  const owner = useAuthOwner();
  const capture = (body: SetApiKeyIn) => ({ body: { ...body }, owner });
  const mutation = useMutation({
    mutationFn: async (request: ReturnType<typeof capture>) => {
      if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before changing your API key.");
      return api.post<SettingsOut>("/settings/api-key", request.body);
    },
    onSuccess: (settings, request) => {
      if (!request.owner.isCurrent()) return;
      queryClient.setQueryData(["settings"], settings);
      // Clear the previous key's record while the current ledger is read.
      if (!request.owner.isCurrent()) return;
      void queryClient.resetQueries({ queryKey: ["quota"], exact: true });
    },
  });
  return { ...mutation,
    mutate: (body: SetApiKeyIn, options?: Parameters<typeof mutation.mutate>[1]) => mutation.mutate(capture(body), options),
    mutateAsync: (body: SetApiKeyIn, options?: Parameters<typeof mutation.mutateAsync>[1]) => mutation.mutateAsync(capture(body), options),
  };
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();
  const owner = useAuthOwner();
  const capture = () => ({ owner });
  const mutation = useMutation({
    mutationFn: async (request: ReturnType<typeof capture>) => {
      if (!request.owner.isCurrent()) throw new ApiError(401, "unauthorized", "Sign in again before changing your API key.");
      return api.delete<SettingsOut>("/settings/api-key");
    },
    onSuccess: (settings, request) => {
      if (!request.owner.isCurrent()) return;
      queryClient.setQueryData(["settings"], settings);
      if (!request.owner.isCurrent()) return;
      void queryClient.resetQueries({ queryKey: ["quota"], exact: true });
    },
  });
  return { ...mutation,
    mutate: (_input: undefined, options?: Parameters<typeof mutation.mutate>[1]) => mutation.mutate(capture(), options),
    mutateAsync: (_input?: undefined, options?: Parameters<typeof mutation.mutateAsync>[1]) => mutation.mutateAsync(capture(), options),
  };
}
