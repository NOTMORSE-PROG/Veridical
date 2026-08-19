// Dashboard data layer (F8.8 first slice, screen 4e): KPI cards +
// manuscripts table.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PaginatedManuscripts, ProgramOut } from "../api/types";

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
  // V-038: how many of the checked manuscripts above have a real
  // recorded decision.
  decided_count: number;
}

export function useDashboardStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: () => api.get<DashboardStats>("/stats"),
  });
}

// V-062 (AC5): `program` is optional -- omitted entirely (not sent as an
// empty string) when the filter is cleared, matching the query builder
// pattern `useRerunEstimate` already uses.
export function useManuscriptsPage(page: number, pageSize = 20, program?: string) {
  const query = program ? `&program=${encodeURIComponent(program)}` : "";
  return useQuery({
    queryKey: ["manuscripts", "table", page, pageSize, program ?? null],
    queryFn: () =>
      api.get<PaginatedManuscripts>(`/manuscripts?page=${page}&page_size=${pageSize}${query}`),
  });
}

// V-062 (AC5): backs the dashboard's program filter -- the list itself
// (CS/IT today) is data, not something the frontend hardcodes.
export function usePrograms() {
  return useQuery({
    queryKey: ["programs"],
    queryFn: () => api.get<ProgramOut[]>("/programs"),
  });
}
