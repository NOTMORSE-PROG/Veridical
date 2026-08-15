// V-042 (screen 4u) — read-only thresholds + live prompt/model version
// transparency. Quota detail reuses the existing `useQuota()`; profile
// reuses `useMe()` -- neither is duplicated here.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { SettingsOut } from "../api/types";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingsOut>("/settings"),
  });
}
