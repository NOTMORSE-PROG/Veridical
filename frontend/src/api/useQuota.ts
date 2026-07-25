// Dashboard quota meter (V-009's GET /quota) — real numbers, never an
// invented "0%" placeholder (charter rule 3: honesty compounds).
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { QuotaStatus } from "./types";

export function useQuota() {
  return useQuery({
    queryKey: ["quota"],
    queryFn: () => api.get<QuotaStatus>("/quota"),
    refetchInterval: 60_000,
  });
}
