// Dashboard data layer (F8.8 first slice, screen 4e): KPI cards +
// manuscripts table.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PaginatedManuscripts } from "../api/types";

export interface DashboardStats {
  manuscripts_checked: number;
  ready_count: number;
  conditionally_ready_count: number;
  not_ready_count: number;
  needs_review_count: number;
  escalations_awaiting_review: number;
  escalation_rate: number | null;
  escalation_budget: number;
  system_underperforming: boolean;
}

export function useDashboardStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: () => api.get<DashboardStats>("/stats"),
  });
}

export function useManuscriptsPage(page: number, pageSize = 20) {
  return useQuery({
    queryKey: ["manuscripts", "table", page, pageSize],
    queryFn: () =>
      api.get<PaginatedManuscripts>(`/manuscripts?page=${page}&page_size=${pageSize}`),
  });
}
