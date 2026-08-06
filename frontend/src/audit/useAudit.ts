// Audit log data layer (F8.10, screen 4s).
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuditLogDetail, PaginatedAuditLog } from "../api/types";

export interface AuditLogFilters {
  checkRunId?: number;
  eventType?: string;
  eventTypePrefix?: string;
  dateFrom?: string;
  dateTo?: string;
}

function buildQuery(filters: AuditLogFilters, page: number, pageSize: number): string {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (filters.checkRunId !== undefined) params.set("check_run_id", String(filters.checkRunId));
  if (filters.eventType) params.set("event_type", filters.eventType);
  if (filters.eventTypePrefix) params.set("event_type_prefix", filters.eventTypePrefix);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

export function useAuditLogPage(filters: AuditLogFilters, page: number, pageSize = 25) {
  return useQuery({
    queryKey: ["audit-log", filters, page, pageSize],
    queryFn: () => api.get<PaginatedAuditLog>(`/audit?${buildQuery(filters, page, pageSize)}`),
    placeholderData: keepPreviousData,
  });
}

export function useAuditLogDetail(id: number | null) {
  return useQuery({
    queryKey: ["audit-log", "detail", id],
    queryFn: () => api.get<AuditLogDetail>(`/audit/${id}`),
    enabled: id !== null,
  });
}
