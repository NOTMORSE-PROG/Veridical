// Dashboard data layer (F8.8 first slice, screen 4e): KPI cards +
// manuscripts table.
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { GroupCollisionOut, PaginatedManuscripts, ProgramOut } from "../api/types";

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

// V-063 (owner's call, 2026-08-19): a READ-ONLY pre-check the confirm
// form calls WHILE the instructor edits the group name/members, so rule
// 3's "propose the match, do not apply it" collision is disclosed BEFORE
// they confirm, not only after via a disambiguating suffix. Caller is
// expected to debounce `shortName`/`memberNames` itself (same manual
// setTimeout pattern `RerunModal.tsx` already uses) -- this hook just
// gates on there being something real to check.
export function useGroupCollisionCheck(shortName: string, memberNames: string[]) {
  const trimmed = shortName.trim();
  const realMembers = memberNames.map((m) => m.trim()).filter((m) => m.length > 0);
  return useQuery({
    queryKey: ["group-collision", trimmed, [...realMembers].sort()],
    queryFn: () => {
      const params = new URLSearchParams({ short_name: trimmed });
      for (const name of realMembers) params.append("member_names", name);
      return api.get<GroupCollisionOut>(`/groups/collision-check?${params.toString()}`);
    },
    enabled: trimmed.length > 0 && realMembers.length > 0,
  });
}
